# AWS Simulated Client

## Example

```python
from simclient.client import ClientMaker
from simclient.policy import PolicyContainer, SimulatedClient

cm = ClientMaker(profile_name="admin")              # like a boto session
pc = PolicyContainer(arn=cm.caller_arn, cm=cm)      # gets principal's policies
print(pc.simulate(actions=["iam:CreateUser"]))      # simulates action(s) using IAM simulation APIs

iam = SimulatedClient("iam", profile_name="admin")  # like a boto3 client, but actions are simulated
print(iam.create_user())                            # call IAM boto3 service method like normal (but no args)
```
