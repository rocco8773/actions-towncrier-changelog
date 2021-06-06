import json
import os
import sys
import toml

from collections import OrderedDict
from github import Github
from typing import Any, Dict, List, Optional, Tuple, Union

_template_fname = "towncrier:default"
_default_types = OrderedDict([
    (u"feature", {"name": u"Features", "showcontent": True}),
    (u"bugfix", {"name": u"Bugfixes", "showcontent": True}),
    (u"doc", {"name": u"Improved Documentation", "showcontent": True}),
    (u"removal", {"name": u"Deprecations and Removals", "showcontent": True}),
    (u"misc", {"name": u"Misc", "showcontent": False})])


# This was from towncrier._settings before they changed the API to be too
# painful.
def parse_toml(config) -> Dict[str, Any]:
    """
    Examine the pyproject.toml and extract the necessary configuration values
    for checking for change log entries.
    """
    try:
        config = config["tool"]["towncrier"]
    except KeyError:
        raise KeyError("No [tool.towncrier] section found in the pyproject.toml")

    sections = OrderedDict()
    types = OrderedDict()

    if "section" in config:
        for x in config["section"]:
            sections[x.get("name", "")] = x["path"]
    else:
        sections[""] = ""

    if "type" in config:
        for x in config["type"]:
            types[x["directory"]] = {
                "name": x["name"],
                "showcontent": x["showcontent"],
            }
    else:
        types = _default_types

    return {
        "package": config.get("package", ""),
        "package_dir": config.get("package_dir", "."),
        "directory": config.get("directory", None),
        "sections": sections,
        "types": types,
    }


def collect_possible_changelog_files(
    pr_files: List[str], config: Dict[str, Any]
) -> List[str]:
    """
    Scan through the ``pr_files``, which should be the files modified by the
    PR, and collect those that are found in the `towncrier`
    changelog/newsfragment directory.
    """
    possible_cl_files = []

    section_dirs = list(config["sections"].values())

    if config["directory"] is not None:
        cl_base_dir = config["directory"]
    else:
        # fallback to towncrier default
        cl_base_dir = os.path.join(
            config['package_dir'], config['package'], "newsfragments"
        )

    for filename in pr_files:
        if not filename.endswith(".rst"):
            continue

        if not filename.startswith(cl_base_dir):
            continue

        filename = filename.lstrip(cl_base_dir).lstrip("/")

        for sdir in section_dirs:
            if filename.startswith(sdir):
                filename.lstrip(sdir).lstrip("/")
                continue

        if filename != "":
            possible_cl_files.append(filename)

    return possible_cl_files


def validate_cl_candidates(filenames: List[str], pr_num, types) -> bool:

    def strip_if_integer_string(s) -> Union[str, int]:
        try:
            i = int(s)
        except ValueError:
            return s

        return str(i)

    # copied directly from towncrier/_builder.py in v21.3.1
    #
    # Returns ticket, category and counter or (None, None, None) if the basename
    # could not be parsed or doesn't contain a valid category.
    def parse_newfragment_basename(
        basename: str, definitions: List[str]
    ) -> Tuple[Optional[str, int], Optional[str], Optional[int]]:
        invalid = (None, None, None)
        _parts = basename.split(".")

        if len(_parts) == 1:
            return invalid
        if len(_parts) == 2:
            ticket, category = _parts
            ticket = strip_if_integer_string(ticket)
            return (ticket, category, 0) if category in definitions else invalid

        # There are at least 3 parts. Search for a valid category from the second
        # part onwards.
        # The category is used as the reference point in the parts list to later
        # infer the issue number and counter value.
        for i in range(1, len(_parts)):
            if _parts[i] in definitions:
                # Current part is a valid category according to given definitions.
                category = _parts[i]
                # Use the previous part as the ticket number.
                # NOTE: This allows news fragment names like fix-1.2.3.feature or
                # something-cool.feature.ext for projects that don't use ticket
                # numbers in news fragment names.
                ticket = strip_if_integer_string(_parts[i - 1])
                counter = 0
                # Use the following part as the counter if it exists and is a valid
                # digit.
                if len(_parts) > (i + 1) and _parts[i + 1].isdigit():
                    counter = int(_parts[i + 1])
                return ticket, category, counter
        else:
            # No valid category found.
            return invalid

    valid = True
    if len(filenames) == 0:
        valid = False
        print(
            f"No changelog files was added in the correct directories "
            f"for PR {pr_num}."
        )

    for filename in filenames:
        if os.path.dirname(filename) != "":
            valid = False
            print(
                f"{filename}  -  INVALID  -  File defined in an unrecognized"
                f" sub-directory"
            )
            continue

        parts = parse_newfragment_basename(filename, types)
        print(f"{filename} and {parts}")

        if parts == (None, None, None) or int(parts[0]) != pr_num:
            valid = False
            print(
                f"{filename}  -  INVALID  -  File name either has wrong PR number"
                f" or change log type"
            )
            continue

        print(f"{filename}  -  VALID")

    if not valid:
        print(
            f"\nSome files were not valid.  Look to the pyproject.toml for proper"
            f"change log sub-directories and types.  Sections labeled as"
            f" [tool.towncrier.section] indicate valid sub-directory names "
            f"and [tool.towncrier.type] indicate valid change log types."
        )

    return valid


def run():
    """Function to run when action is run."""
    event_name = os.environ['GITHUB_EVENT_NAME']
    if not event_name.startswith('pull_request'):
        print(f'No-op for {event_name}')
        sys.exit(0)

    g = Github(os.environ.get('GITHUB_TOKEN'))

    # get webhook paylod for the GitHub event
    event_jsonfile = os.environ['GITHUB_EVENT_PATH']
    with open(event_jsonfile, encoding='utf-8') as fin:
        event = json.load(fin)

    bot_username = os.environ.get('BOT_USERNAME', 'astropy-bot')
    base_repo_name = event['pull_request']['base']['repo']['full_name']

    # Grab config from upstream's default branch
    print(
        f"Bot username: {bot_username}\n"
        f"Base repository: {base_repo_name}\n\n"
    )
    base_repo = g.get_repo(base_repo_name)
    toml_cfg = toml.loads(
        base_repo.get_contents('pyproject.toml').decoded_content.decode('utf-8')
    )

    try:
        cl_config = toml_cfg['tool'][bot_username]['towncrier_changelog']
    except KeyError:
        print(f'Missing [tool.{bot_username}.towncrier_changelog] section.')
        sys.exit(1)

    if not cl_config.get('enabled', False):
        print(
            f"Skipping towncrier changelog plugin as disabled in config"
            f" (i.e. 'enabled = false' for the"
            f" [tool.{bot_username}.towncrier_changelog] in the"
            f" pyproject.toml on the base, not the PR branch)."
        )
        sys.exit(0)

    pr_labels = [e['name'] for e in event['pull_request']['labels']]
    print(f'PR labels: {pr_labels}\n')

    skip_label = cl_config.get('changelog_skip_label', None)
    if skip_label and skip_label in pr_labels:
        print(f'Skipping towncrier changelog plugin because "{skip_label}" '
              'label is set.')
        sys.exit(0)

    config = parse_toml(toml_cfg)
    pr_num = event['number']
    pr = base_repo.get_pull(pr_num)
    pr_modified_files = [f.filename for f in pr.get_files()]

    cl_condidates = collect_possible_changelog_files(pr_modified_files, config)
    print(
        f"candidates = {cl_condidates}\n"
        f"PR num = {pr_num}\n"
        f"types = {list(config['types'])}"
    )
    valid = validate_cl_candidates(
        cl_condidates, pr_num=pr_num, types=list(config["types"])
    )

    if not valid:
        print(f"Some change log files are incorrect.  See report above.")
        sys.exit(1)

    # Success!
    print(f"Change log file(s) detected for PR {pr_num} and all are valid.")


if __name__ == "__main__":
    run()
