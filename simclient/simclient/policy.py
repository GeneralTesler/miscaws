import json

from .client import BotoResource, ARN, ClientMaker, unsigned_client


def arn_to_iam_resource(arn: ARN, cm: ClientMaker) -> BotoResource:
    """ given an ARN of an IAM user or role, return the corresponding boto3 resource"""
    iam = cm.resource(service="iam")
    if arn.resource_type == "user":
        return iam.User(arn.resource_id)
    elif arn.resource_type == "role":
        return iam.Role(arn.resource_id)
    else:
        raise NotImplementedError()


def get_scps_for_account(account_number: str, cm: ClientMaker) -> list:
    """return of a list of SCP documents for the given account
    NOTE: this can only be performed from the org master account
    """
    scps = cm.call(
        service="organizations",
        action="list_policies_for_target",
        response_key="Policies",
        action_args={"TargetId": account_number, "Filter": "SERVICE_CONTROL_POLICY"},
        all_regions=False,
    )
    org = cm.client("organizations")
    return [org.describe_policy(PolicyId=scp["Id"])["Policy"]["Content"] for scp in scps]


def merge_policy_dicts(policies: list) -> dict:
    """ merge multiple policies into one policy by pulling out all statements from all policies """
    merged_policy = {"Version": "2012-10-17", "Statement": []}
    for policy in policies:
        if type(policy) == str:
            policy = json.loads(policy)
        merged_policy["Statement"].extend(policy["Statement"])

    return merged_policy


def get_policy_documents_for_resource(resource: BotoResource) -> list:
    """ given an IAM user or role resource instance, return all inline and attached policies"""
    policies = []
    # attached policies
    #   attached policy collection is of type iam.Policy so you need to retrieve the default version
    #   to get the document
    for policy in resource.attached_policies.all():
        policies.append(policy.default_version.document)
    # inline policies
    #   inline policies provide the document directly
    for policy in resource.policies.all():
        policies.append(policy.policy_document)

    return policies


def get_group_policy_documents_for_resource(resource: BotoResource) -> list:
    """given an IAM user or role, return all inline and attached policies
    roles cannot have groups so they are simply ignored
    """
    policies = []
    if hasattr(resource, "groups"):
        for group in resource.groups.all():
            policies.extend(get_policy_documents_for_resource(resource=group))
    return []


class PolicyContainer:
    """
    Provided with an IAM user/role ARN, gather all attached policies, inline policies, permission boundaries,
        and (optionally) SCPs (specifically for that principal's account)
        For IAM users, also gathers the policies for the user's groups

    Permission boundarys and SCPs are merged into a single policy for compatibility with the IAM simulation APIs
    """

    def __init__(self, arn: ARN, cm: ClientMaker, collect_scps: bool = True):
        iam = cm.client("iam")
        policies = []
        negative_policies = []

        resource = arn_to_iam_resource(arn=arn, cm=cm)
        # direct inline and attached
        policies.extend(get_policy_documents_for_resource(resource=resource))
        # group inline and attached
        policies.extend(get_group_policy_documents_for_resource(resource=resource))

        # permission boundary policies
        if resource.permissions_boundary:
            # resource -> policy -> default policy version -> document
            negative_policies.extend(
                [iam.Policy(resource.permissions_boundary["PermissionsBoundaryArn"]).default_version.document]
            )

        if collect_scps:
            # should enforce account id on provided arn for this function
            account_number = arn.account_id if arn.account_id else cm.account_number
            negative_policies.extend(get_scps_for_account(account_number=arn.account_id, cm=cm))

        self.policies: list = policies
        # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/iam.html#IAM.Client.simulate_custom_policy
        #   PermissionsBoundaryPolicyInputList is a list but the documentation specified it only allows 1 policy
        self.negative_policies: list = (
            [merge_policy_dicts(policies=negative_policies)] if len(negative_policies) > 0 else None
        )
        self.cm: ClientMaker = cm
        self.arn: ARN = arn

    def simulate(self, actions: list, **sim_args):
        """ simulate an action against the gathered policies using the IAM: SimulateCustomPolicy API """
        policies = [json.dumps(policy) for policy in self.policies]
        negative_policies = [json.dumps(policy) for policy in self.negative_policies] if self.negative_policies else []
        sim_result = self.cm.client("iam").simulate_custom_policy(
            PolicyInputList=policies,
            ActionNames=actions,
            PermissionsBoundaryPolicyInputList=negative_policies,
            CallerArn=str(self.arn),
            **sim_args,
        )
        decision = sim_result["EvaluationResults"][0]["EvalDecision"]
        return decision


class SimulatedClient:
    """
    This class provides a mechanism for mock-calling arbitrary AWS actions
    Instead of actually calling the action, it will call IAM:SimulateCustomPolicy

    On creating a mocked client, a new PolicyContainer class will be created for the caller principal
    When a method of the mocked client is called, a function that simulates the action using the PolicyContainer is
        returned

    The purpose of this class is to provide an interface for interacting with the PolicyContainer simulation
        using boto3 client conventions; all args passed to the client method are ignored

    This client will ignore SCPs by default
    Note that this is different from the default behavior of the PolicyContainer class

    Example:
        ```
        >>> ec2 = SimulatedClient("ec2")
        >>> ec2.describe_instances()
        'allowed'
        ```
    """

    def __init__(self, service: str, collect_scps: bool = False, **cm_args):

        self._service = service
        self._cm = ClientMaker(**cm_args)
        self._pc = PolicyContainer(arn=self._cm.caller_arn, cm=self._cm, collect_scps=collect_scps)

    def __getattr__(self, operation):
        def simulate_api(*args, **kwargs):
            # ex: create_user -> CreateUser
            # easiest way to get the boto3 action <-> IAM action seems to be to create a client then pull the mapping
            #   this is the opposite of botocore xform_name
            method = unsigned_client(service=self._service).meta.method_to_api_mapping[operation]
            action = f"{self._service.lower()}:{method}"
            return self._pc.simulate(actions=[action])

        return simulate_api
