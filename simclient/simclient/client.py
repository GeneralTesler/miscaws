import json
import boto3
from boto3.resources.base import ServiceResource
from botocore import UNSIGNED
from botocore.client import BaseClient
from botocore.config import Config as BotocoreConfig
from botocore.exceptions import ClientError
from functools import lru_cache
from typing import Callable, TypeVar
from dataclasses import dataclass

from .log import logger
from .util import merge_dicts

BotoClient = TypeVar("BotoClient", bound=BaseClient)
BotoResource = TypeVar("BotoResource", bound=ServiceResource)


def log_params(params, **kwargs):
    """log all boto3 API calls to the standard logger
    information logged: service, API, request parameters, and region
    """
    logger.info(
        json.dumps(
            {
                "service": kwargs.get("event_name").split(".")[-2],
                "operation": kwargs.get("event_name").split(".")[-1],
                "params": params,
                "region": kwargs.get("context")["client_region"],
            }
        )
    )


def unsigned_client(service: str) -> BotoClient:
    """ Returns an unsigned boto3 client """
    return boto3.client(service, config=BotocoreConfig(signature_version=UNSIGNED))


def split_arn_string(arn: str) -> list:
    """ split an ARN into a list and handle complex resource ids"""
    parts = arn.split(":")
    if len(parts) == 6:
        if "/" in parts[5]:
            resource = parts[5].split("/")
            parts[5] = resource[0].lower()
            parts.append("/".join(resource[1:]))
        else:
            parts[6] = ""
    return parts


@dataclass
class ARN:
    """ AWS ARN """

    prefix: str = None
    partition: str = None
    service: str = None
    region: str = None
    account_id: str = None
    resource_type: str = None
    resource_id: str = None

    @classmethod
    def from_string(cls, arn: str):
        return cls(*split_arn_string(arn=arn))

    def __str__(self):
        return f"{self.prefix}:{self.partition}:{self.service}:{self.region}:{self.account_id}:{self.resource_type}/{self.resource_id}"


class ClientMaker:
    """
    extended boto session
    """

    def __init__(self, param_function: Callable = log_params, user_agent: str = "simclient", **session_args):
        self.session = boto3.session.Session(**session_args)
        # this function will receive the parameters for all API calls
        #   the default function will log all request parameters + service + api to the configured log file
        # any clients/resources created through this class will have this function registered
        self.session.events.register("provide-client-params.*.*", param_function)
        self._user_agent = user_agent

    def _gen_config(self, **config_args):
        """method of generating a standard config for boto3 sessions/clients
        mainly used for user-agent changing
        """
        return BotocoreConfig(user_agent=self._user_agent, **config_args)

    def _gen_session_resource(self, resource_type: str, resource_args: dict = None, config_args: dict = None):
        """ generate a session resource (boto3 client, boto3 resource) """
        if hasattr(self.session, resource_type):
            resource = getattr(self.session, resource_type)
        else:
            raise Exception("unknown resource type")
        config_args = {} if not config_args else config_args
        resource_args = {} if not resource_args else resource_args
        return resource(config=self._gen_config(**config_args), **resource_args)

    def client(self, service: str, client_args: dict = None, config_args: dict = None) -> BotoClient:
        """ generates a client of the provided service and sets the user-agent """
        return self._gen_session_resource(
            resource_type="client",
            config_args=config_args,
            resource_args=merge_dicts(client_args, {"service_name": service}),
        )

    def resource(self, service: str, resource_args: dict = None, config_args: dict = None) -> BotoResource:
        """ generate a resource of the provided resource type and sets the user-agent """
        return self._gen_session_resource(
            resource_type="resource",
            config_args=config_args,
            resource_args=merge_dicts(resource_args, {"service_name": service}),
        )

    @lru_cache(maxsize=None)
    def _get_caller_identity(self, **client_args):
        """ call STS:GetCallerIdentity using extended session """
        return self.client(service="sts", **client_args).get_caller_identity()

    @property
    def caller_arn_str(self) -> str:
        """ caller identity """
        return self._get_caller_identity()["Arn"]

    @property
    def account_number(self) -> str:
        """ caller account number """
        return self._get_caller_identity()["Account"]

    @property
    def caller_arn(self) -> ARN:
        """ caller ARN as ARN class instance """
        return ARN.from_string(arn=self.caller_arn_str)

    @property
    def enabled_regions(self) -> list:
        """ returns list of region names that are enabled """
        return [
            region["RegionName"]
            for region in self.client(service="ec2").describe_regions(
                AllRegions=True,
                Filters=[
                    {
                        "Name": "opt-in-status",
                        "Values": ["opt-in-not-required", "opted-in"],
                    }
                ],
            )["Regions"]
        ]

    def is_region_available(self, region: str) -> bool:
        """Determines if provided region is enabled by attempting to call STS:GetCallerIdentity
        in that region. This call shouldnt fail unless the credentials are invalid or the
        region is disabled
        """
        try:
            self._get_caller_identity(region_name=region)
            return True
        except ClientError:
            return False
        finally:
            raise Exception()

    def call(
        self,
        service: str,
        action: str,
        response_key: str,
        all_regions: bool = True,
        region: str = None,
        jmes_filter: str = None,
        action_args: dict = None,
        client_args: dict = None,
    ):
        """
        Call an AWS API with automatic paginator and multi-region support

        :param service: service name
        :param action: API action name (ex DescribeInstances)
        :param response_key: dict key name for response data
        :param all_regions: Yes/no call API in all regions
        :param region: if not in all regions, provide region or use default
        :param jmes_filter: if there is a paginator, filter results using jmespath
        :param action_args: arguments for the API
        :param client_args: arguments for the each instance of the service client
        """
        action_args = {} if not action_args else action_args
        if all_regions:
            regions = self.enabled_regions
        else:
            if region:
                regions = [region]
            elif self.session.region_name:
                regions = [self.session.region_name]
            else:
                regions = [self.client("sts").meta.region_name]

        results = []
        for region in regions:
            client = self.client(
                service=service,
                config_args={"region_name": region},
                client_args=client_args,
            )
            if client.can_paginate(action):
                paginator = client.get_paginator(action)
                iterator = paginator.paginate(**action_args)
                if jmes_filter:
                    [results.extend(data) for data in iterator.search(jmes_filter)]
                else:
                    [results.extend(data[response_key]) for data in iterator]
            else:
                results.extend(getattr(client, action)(**action_args)[response_key])

        return results
