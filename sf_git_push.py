import json
import os
import re
from pathlib import Path
from typing import List, Optional, Union
import git

from sf_git.models import Worksheet, WorksheetError, AuthenticationMode
from sf_git.git_utils import get_tracked_files, get_blobs_content
import sf_git.snowsight_auth
import sf_git.worksheets_utils


def set_output(name, value):
    """
    Set key value pair in the gitHub action output

    :param name: name of output to set
    :param value: value to set
    """
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"{name}={value}")
    else:
        print(f"::set-output name={name}::{value}")


def parse_str_as_list(csv_string) -> List[str]:
    """
    Parse a comma separated value string to list of strings, trimmed.

    ex: 'hello, snowflake ,git' -> ['hello', 'snowflake', 'git']

    :param csv_string: CSV string

    :return: list of string values trimmed
    """
    values = csv_string.split(",")
    return [v.strip() for v in values]


def load_worksheets_from_cache(
    repo: git.Repo,
    worksheets_path: Path,
    branch_name: Optional[str] = None,
    only_folder: Optional[Union[str, Path]] = None,
) -> List[Worksheet]:
    """
    Load worksheets from cache.

    :param repo: Git repository as it only considers tracked files
    :param worksheets_path: absolute worksheet path, must be within repo path
    :param branch_name: name of git branch to get files from
    :param only_folder: to get only worksheets in that folder

    :return: list of tracked worksheet objects
    """

    print(f"[Worksheets] Loading from {worksheets_path}")
    if not os.path.exists(worksheets_path):
        raise WorksheetError(
            "Could not retrieve worksheets from cache. "
            f"The folder {worksheets_path} does not exist"
        )

    tracked_files = [
        f for f in get_tracked_files(repo, worksheets_path, branch_name)
    ]

    # filter on worksheet files
    ws_metadata_files = [
        f for f in tracked_files if f.name.endswith("_metadata.json")
    ]
    if len(ws_metadata_files) == 0:
        return []

    # map to worksheet objects
    worksheets = []
    metadata_contents = get_blobs_content(ws_metadata_files)

    for wsf in metadata_contents.values():
        ws_metadata = json.loads(wsf)
        if only_folder and ws_metadata["folder_name"] != only_folder:
            continue
        current_ws = Worksheet(
            ws_metadata["_id"],
            ws_metadata["name"],
            ws_metadata["folder_id"],
            ws_metadata["folder_name"],
            content_type=ws_metadata.get("content_type", "sql"),
        )
        extension = "py" if current_ws.content_type == "python" else "sql"
        content_filename = re.sub(
            r"[ :/]", "_", f"{ws_metadata['name']}.{extension}"
        )

        try:
            content_blob = next(
                f for f in tracked_files if f.name == content_filename
            )
        except StopIteration:
            tracked_files = [f.name for f in tracked_files]
            print(f"{content_filename} not found in {tracked_files}")
            return []

        ws_content_as_dict = get_blobs_content([content_blob])
        ws_content = list(ws_content_as_dict.values())[0]
        current_ws.content = ws_content
        worksheets.append(current_ws)

    return worksheets


if __name__ == "__main__":

    # Set push parameters
    worksheets_path = os.environ.get("ACTION_WORKSHEETS_PATH", "Not found")
    accounts = parse_str_as_list(os.environ.get("ACTION_SF_ACCOUNTS"))
    usernames = parse_str_as_list(os.environ.get("ACTION_SF_USERNAMES"))
    passwords = parse_str_as_list(os.environ.get("ACTION_SF_PASSWORDS"))
    auth_mode = "PWD"
    only_folder = os.environ.get("ACTION_ONLY_FOLDER")
    branch = os.environ.get("ACTION_BRANCH_NAME")
    params = {
        "worksheets_path": worksheets_path,
        "accounts": accounts,
        "usernames": usernames,
        "passwords": passwords,
        "auth_mode": auth_mode,
        "only_folder": only_folder,
    }

    # Get git repo
    print("Loading git repo")
    git_root = os.environ.get("ACTION_GIT_ROOT")
    current_repo = git.Repo(git_root)

    # Get worksheets
    print("Loading worksheets")
    worksheets = load_worksheets_from_cache(
        repo=current_repo,
        worksheets_path=Path(git_root) / worksheets_path,
        branch_name=branch,
        only_folder=only_folder,
    )

    sf_git.worksheets_utils.print_worksheets(worksheets, logger=print)

    # Push worksheets
    print("Uploading to SnowSight")
    upload_report = {}

    for i, account in enumerate(accounts):
        username = usernames[i]
        password = passwords[i]
        auth_context = sf_git.snowsight_auth.authenticate_to_snowsight(
            account, username, password, auth_mode=AuthenticationMode.PWD
        )

        upload_report[f"account_{i}"] = (
            sf_git.worksheets_utils.upload_to_snowsight(
                auth_context, worksheets
            )
        )

    print(upload_report)
    set_output("upload_report", str(upload_report))
