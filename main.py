import boto3
import json
import os
import sys
from diagrams import Cluster, Diagram
from diagrams.aws.compute import ECS, EKS, Lambda
from diagrams.aws.management import OrganizationsAccount
from diagrams.aws.security import IAMPermissions
from diagrams.generic.blank import Blank

os.environ["AWS_DEFAULT_REGION"] = os.getenv("REGION", "us-east-1")
GRANULAR = int(os.getenv("GRANULAR", "0")) == 1


def list_services_from_iam_policy(policy_arn=None, policy_document=None):
    services = set()

    if policy_arn:
        # Fetch the policy document using boto3 if policy_arn is provided
        iam_client = boto3.client("iam")
        response = iam_client.get_policy_version(
            PolicyArn=policy_arn, VersionId="v1"  # Assuming we want the latest version
        )
        policy_document = response["PolicyVersion"]["Document"]
    elif policy_document:
        # If policy_document is provided as a string, parse it
        if isinstance(policy_document, str):
            policy_document = json.loads(policy_document)
    else:
        raise ValueError("Either policy_arn or policy_document must be provided")

    # Extract services from the policy document
    for statement in policy_document.get("Statement", []):
        action = statement.get("Action", [])
        if isinstance(action, str):
            action = [action]
        for a in action:
            service = a.split(":")[0]
            services.add(service)

    return list(services)


def get_idc_permission_sets(account_id):
    sso = boto3.client("sso-admin")
    idstore = boto3.client("identitystore")
    instance_arn = os.getenv("INSTANCE_ARN")
    id_store_id = os.getenv("DIRECTORY_STORE")
    try:
        permission_sets = sso.list_permission_sets_provisioned_to_account(
            AccountId=account_id,
            InstanceArn=instance_arn,
        ).get("PermissionSets", [])
        results = []
        for p in permission_sets:
            meta = sso.describe_permission_set(
                InstanceArn=instance_arn, PermissionSetArn=p
            ).get("PermissionSet", {})
            # policies
            try:
                policies = [
                    {"arn": p.get("Arn")}
                    for p in sso.list_managed_policies_in_permission_set(
                        InstanceArn=instance_arn, PermissionSetArn=p
                    ).get("AttachedManagedPolicies", [])
                ]
                for policy in policies:
                    if "arn:aws:iam::aws:policy" in policy.get("arn"):
                        policy["services"] = ["aws managed policy"]
                    else:
                        policy["services"] = list_services_from_iam_policy(
                            policy_arn=policy.get("arn")
                        )
                try:
                    inline_policy = sso.get_inline_policy_for_permission_set(
                        InstanceArn=instance_arn, PermissionSetArn=p
                    ).get("InlinePolicy")
                    if inline_policy:
                        policies.append(
                            {
                                "arn": "inline",
                                "services": list_services_from_iam_policy(
                                    policy_document=inline_policy
                                ),
                            }
                        )
                except:
                    ...
            except:
                policies = []
            identities = [
                {"id": i.get("PrincipalId"), "type": i.get("PrincipalType")}
                for i in sso.list_account_assignments(
                    AccountId=account_id, InstanceArn=instance_arn, PermissionSetArn=p
                ).get("AccountAssignments", [])
            ]
            for ident in identities:
                try:
                    if ident.get("type") == "GROUP":
                        ident["name"] = idstore.describe_group(
                            IdentityStoreId=id_store_id, GroupId=ident.get("id")
                        ).get("DisplayName")
                    else:
                        ident["name"] = idstore.describe_user(
                            IdentityStoreId=id_store_id, UserId=ident.get("id")
                        ).get("UserName")
                except:
                    ident["name"] = "UNKNOWN"

            results.append(
                {
                    "name": meta.get("Name"),
                    "arn": p,
                    "policies": policies,
                    "identities": identities,
                }
            )
        return results
    except Exception as e:
        print(f"An error occurred while fetching permission sets: {e}")
        return None


def handler():
    account_id = os.getenv("ACCOUNT_ID")
    result = get_idc_permission_sets(account_id)
    if not result:
        print("Failed to retrieve permission sets.")
        sys.exit()
    with Diagram("Account Permissions", show=False, direction="TB"):
        source = OrganizationsAccount(account_id)
        for ps in result:
            with Cluster(ps.get("name")):
                permset = IAMPermissions("permission set")
                source >> permset
                with Cluster("Policies"):
                    for policy in ps.get("policies"):
                        with Cluster("{}".format(policy.get("arn").split("/")[-1])):
                            if not GRANULAR:
                                blank = Blank("")
                            else:
                                services = []
                                with Cluster("Services"):
                                    servicep = Blank("\n".join(policy.get("services")))
                with Cluster("Users/Groups"):
                    users = Blank(
                        "\n".join(
                            [
                                "\n".join(
                                    [
                                        x.get("name")[i : i + 12]
                                        for i in range(0, len(x.get("name")), 12)
                                    ]
                                )
                                for x in ps.get("identities")
                            ]
                        )
                    )


handler()
