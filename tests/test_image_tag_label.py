"""The sidebar badge for the Docker image tag (IMAGE_TAG build arg).

A release build passed IMAGE_TAG=3.3.0, and the badge logic only recognised
pre-releases written with a "v", so every stable install would have shown
"v3.3.0 [3.3.0]" in the DEV badge style. Meanwhile :dev images, built from
main since #200, passed IMAGE_TAG=latest and showed no DEV badge at all.

The badge text comes from web/image_tag.py; the workflow step that picks
IMAGE_TAG is run here with bash for each kind of build.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from web.image_tag import tag_label

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "docker.yml").read_text(encoding="utf-8")


@pytest.mark.parametrize("tag,label", [
    ("latest", ""),
    ("", ""),
    ("3.3.0", ""),
    ("v3.3.0", ""),
    ("dev", "DEV"),
    ("v3.4.0-beta1", "BETA 1"),
    ("3.4.0-rc2", "RC 2"),
    ("pr-240", "PR-240"),
])
def test_badge_text(tag, label):
    assert tag_label(tag) == label


def _image_tag_script():
    """The run: block of the 'Compute IMAGE_TAG build arg' step."""
    step = WORKFLOW[WORKFLOW.index("- name: Compute IMAGE_TAG build arg"):]
    lines = step[step.index("run: |"):].splitlines()[1:]
    indent = len(lines[0]) - len(lines[0].lstrip())
    body = []
    for line in lines:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:])
    return "\n".join(body)


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
@pytest.mark.parametrize("event,ref,prerelease,expected", [
    ("push", "refs/heads/main", "", "dev"),
    ("push", "refs/heads/dev", "", "dev"),
    ("release", "refs/tags/v3.3.0", "false", "latest"),
    ("release", "refs/tags/v3.4.0-beta1", "true", "v3.4.0-beta1"),
])
def test_workflow_picks_the_image_tag(tmp_path, event, ref, prerelease, expected):
    # A relative GITHUB_OUTPUT: Git Bash on Windows rewrites env values that look like paths
    env = {**os.environ, "GITHUB_EVENT_NAME": event, "GITHUB_REF": ref, "PRERELEASE": prerelease,
           "GITHUB_OUTPUT": "github_output"}
    # Full path: on Windows a bare "bash" can start WSL's, which drops the environment
    subprocess.run([shutil.which("bash"), "-c", _image_tag_script()], env=env, cwd=tmp_path, check=True)
    assert (tmp_path / "github_output").read_text().strip() == f"value={expected}"
    assert tag_label(expected) == {"dev": "DEV", "latest": "", "v3.4.0-beta1": "BETA 1"}[expected]
