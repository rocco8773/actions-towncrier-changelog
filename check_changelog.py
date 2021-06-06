import json
import os
import re
import sys
import toml

from collections import OrderedDict
from github import Github
from pathlib import Path


_template_fname = "towncrier:default"
_default_types = OrderedDict([
    (u"feature", {"name": u"Features", "showcontent": True}),
    (u"bugfix", {"name": u"Bugfixes", "showcontent": True}),
    (u"doc", {"name": u"Improved Documentation", "showcontent": True}),
    (u"removal", {"name": u"Deprecations and Removals", "showcontent": True}),
    (u"misc", {"name": u"Misc", "showcontent": False})])


# This was from towncrier._settings before they changed the API to be too
# painful.
def parse_toml(config):
    """
    Examine the pyproject.toml and extract the necessary configuration values
    for checking for change log entries.
    """
    try:
        config = config["tool"]["towncrier"]
    except KeyError:
        raise KeyError("No [tool.towncrier] section found in the pyproject.toml")

    sections = OrderedDict()
    types = OrderedDict(**_default_types)

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

    return {
        "package": config.get("package", ""),
        "package_dir": config.get("package_dir", "."),
        "directory": config.get("directory"),
        "sections": sections,
        "types": types,
    }


def calculate_fragment_paths(config):
    if config.get("directory"):
        base_directory = config["directory"]
        fragment_directory = None
    else:
        base_directory = os.path.join(config['package_dir'], config['package'])
        fragment_directory = "newsfragments"

    section_dirs = []
    for key, val in config['sections'].items():
        if fragment_directory is not None:
            section_dirs.append(os.path.join(base_directory, val,
                                             fragment_directory))
        else:
            section_dirs.append(os.path.join(base_directory, val))

    return section_dirs


def check_sections(filenames, sections):
    """Check that a file matches ``<section><issue number>``.
    Otherwise the root dir matches when it shouldn't.
    """
    for section in sections:
        # Make sure the path ends with a /
        if not section.endswith("/"):
            section += "/"
        pattern = section.replace("/", r"\/") + r"\d+.*"
        for fname in filenames:
            match = re.match(pattern, fname)
            if match is not None:
                return fname
    return False


def check_changelog_type(types, matching_file):
    filename = Path(matching_file).name
    components = filename.split(".")
    return components[1] in types


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
        print(f"cl_config = {cl_config}")
        print('Skipping towncrier changelog plugin as disabled in config')
        sys.exit(0)

    pr_labels = [e['name'] for e in event['pull_request']['labels']]
    print(f'PR labels: {pr_labels}\n\n')

    skip_label = cl_config.get('changelog_skip_label', None)
    if skip_label and skip_label in pr_labels:
        print(f'Skipping towncrier changelog plugin because "{skip_label}" '
              'label is set')
        sys.exit(0)

    config = parse_toml(toml_cfg)
    pr_num = event['number']
    pr = base_repo.get_pull(pr_num)
    pr_modified_files = [f.filename for f in pr.get_files()]

    print(f"PR Files include {pr_modified_files}")

    section_dirs = calculate_fragment_paths(config)
    types = config['types'].keys()
    matching_file = check_sections(pr_modified_files, section_dirs)

    if not matching_file:
        print('No changelog file was added in the correct directories for '
              f'PR {pr_num}')
        sys.exit(1)

    if not check_changelog_type(types, matching_file):
        print(f'The changelog file that was added for PR {pr_num} is not '
              f'one of the configured types: {types}')
        sys.exit(1)

    # TODO: Make this a regex to check that the number is in the right place etc.
    if (cl_config.get('verify_pr_number', False) and
            str(pr_num) not in matching_file):
        print(f'The number in the changelog file ({matching_file}) does not '
              f'match this pull request number ({pr_num}).')
        sys.exit(1)

    # Success!
    print(f'Changelog file ({matching_file}) correctly added for PR {pr_num}.')


if __name__ == "__main__":
    run()
