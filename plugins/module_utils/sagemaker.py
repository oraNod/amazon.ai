# Copyright: Contributors to the Ansible project
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ansible_collections.amazon.ai.plugins.module_utils.utils import normalize_url

from ansible_collections.amazon.aws.plugins.module_utils.modules import AnsibleAWSModule
from ansible_collections.amazon.aws.plugins.module_utils.retries import AWSRetry

try:
    from botocore.exceptions import ClientError
except ImportError:
    pass


@AWSRetry.jittered_backoff(retries=10)
def list_tags(client, resource_arn: str) -> Dict[str, str]:
    paginator = client.get_paginator("list_tags")
    tags = paginator.paginate(ResourceArn=resource_arn).build_full_result()["Tags"]
    return {t["Key"]: t["Value"] for t in tags}


@AWSRetry.jittered_backoff(retries=10)
def describe_code_repository(client, repository_name: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve details for a specific SageMaker Code Repository.

    Args:
        client: The boto3 SageMaker client.
        repository_name: The name of the code repository.

    Returns:
        A dictionary with the code repository details if found, otherwise None.

    Raises:
        ClientError: If AWS returns an error other than 'ValidationException'.
    """
    try:
        return client.describe_code_repository(CodeRepositoryName=repository_name)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ValidationException":
            return None
        raise


@AWSRetry.jittered_backoff(retries=10)
def list_code_repositories(client, **params: Any) -> List[Dict[str, Any]]:
    """
    Retrieve a list of SageMaker Code Repositories using pagination.

    Args:
        client: The boto3 SageMaker client.
        **params: Additional filter parameters for the list operation.

    Returns:
        A list of code repository summary dictionaries.
    """
    paginator = client.get_paginator("list_code_repositories")
    return paginator.paginate(**params).build_full_result()["CodeRepositorySummaryList"]


def find_code_repository(client, module: AnsibleAWSModule) -> List[Dict[str, Any]]:
    """
    Find SageMaker Code Repositories, optionally filtering by name.

    Args:
        client: The boto3 SageMaker client.
        module: The Ansible module instance containing user parameters.

    Returns:
        List of code repository detail dictionaries. Empty list if none found.

    Behavior:
        - If `name` is provided in module params, uses `describe_code_repository()`
          to fetch a single repository directly.
        - Otherwise, uses `list_code_repositories()` to enumerate repositories,
          then fetches full details for each via `describe_code_repository()`.
        - Supports optional filtering by `name_contains`, `sort_by`, and `sort_order`.
    """
    repository_name: Optional[str] = module.params.get("name")

    if repository_name:
        repo = describe_code_repository(client, repository_name)
        return [repo] if repo else []

    params: Dict[str, Any] = {}
    if module.params.get("name_contains"):
        params["NameContains"] = module.params["name_contains"]
    if module.params.get("sort_by"):
        params["SortBy"] = module.params["sort_by"]
    if module.params.get("sort_order"):
        params["SortOrder"] = module.params["sort_order"]

    summaries = list_code_repositories(client, **params)
    if not summaries:
        return []

    return [repo for s in summaries if (repo := describe_code_repository(client, s["CodeRepositoryName"])) is not None]


def create_code_repository(client, module: AnsibleAWSModule) -> Tuple[bool, str]:
    """
    Create a new SageMaker Code Repository.

    Args:
        client: The boto3 SageMaker client.
        module: The AnsibleAWSModule instance.

    Returns:
        (changed, message)
    """
    repository_name = module.params["name"]

    if module.check_mode:
        return True, f"Check mode: would have created code repository {repository_name}."

    git_config: Dict[str, Any] = {"RepositoryUrl": module.params["git_config"]["repository_url"]}
    if module.params["git_config"].get("branch"):
        git_config["Branch"] = module.params["git_config"]["branch"]
    if module.params["git_config"].get("secret_arn"):
        git_config["SecretArn"] = module.params["git_config"]["secret_arn"]

    params: Dict[str, Any] = {
        "CodeRepositoryName": repository_name,
        "GitConfig": git_config,
    }

    if module.params.get("tags"):
        params["Tags"] = [{"Key": k, "Value": v} for k, v in module.params["tags"].items()]

    client.create_code_repository(**params)
    return True, f"Code repository {repository_name} created successfully."


def update_code_repository(client, module: AnsibleAWSModule, existing: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Update the secret ARN of an existing SageMaker Code Repository after verifying
    that immutable fields (repository_url, branch, tags) remain unchanged.

    Args:
        client: The boto3 SageMaker client.
        module: The AnsibleAWSModule instance.
        existing: Dict of existing code repository details.

    Returns:
        (changed, message)
    """
    repository_name = module.params["name"]
    existing_git_config = existing["GitConfig"]
    new_git_config = module.params["git_config"]

    if module.params.get("tags") is not None:
        existing_tags = list_tags(client, existing["CodeRepositoryArn"])
        if module.params["tags"] != existing_tags:
            module.warn("Tags cannot be modified after the code repository is created.")

    if normalize_url(existing_git_config["RepositoryUrl"]) != normalize_url(new_git_config["repository_url"]):
        module.warn("The repository URL cannot be updated after the code repository is created.")
    if new_git_config.get("branch") and existing_git_config.get("Branch") != new_git_config["branch"]:
        module.warn("The branch cannot be updated after the code repository is created.")

    if not new_git_config.get("secret_arn") or existing_git_config.get("SecretArn") == new_git_config["secret_arn"]:
        return False, "No updates needed."

    if module.check_mode:
        return True, f"Check mode: would have updated code repository {repository_name}."

    client.update_code_repository(
        CodeRepositoryName=repository_name,
        GitConfig={"SecretArn": new_git_config["secret_arn"]},
    )
    return True, f"Code repository {repository_name} updated successfully."


def delete_code_repository(client, module: AnsibleAWSModule, existing: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Delete an existing SageMaker Code Repository.

    Args:
        client: The boto3 SageMaker client.
        module: The AnsibleAWSModule instance.
        existing: Dict of existing code repository details.

    Returns:
        (changed, message)
    """
    repository_name = existing["CodeRepositoryName"]

    if module.check_mode:
        return True, f"Check mode: would have deleted code repository '{repository_name}'."

    client.delete_code_repository(CodeRepositoryName=repository_name)
    return True, f"Code repository {repository_name} deleted successfully."
