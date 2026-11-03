import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORG = {"!fjcloudaiconsulting/**", "!ghcr.io/fjcloudaiconsulting/**"}


def load(name):
    with open(os.path.join(ROOT, name)) as f:
        return json.load(f)


class WeeklySchedule(unittest.TestCase):
    """INFRA-143: non-major updates wait for the weekly window; release bumps and security fixes never do."""

    def rules(self):
        return [r for name in ("default.json", "app-stack.json") for r in load(name).get("packageRules", [])]

    def test_window_is_in_the_owners_timezone(self):
        self.assertEqual(load("default.json")["timezone"], "Europe/Amsterdam")

    def test_scheduled_rules_skip_majors_and_org_packages(self):
        scheduled = [r for r in self.rules() if "schedule" in r]
        self.assertTrue(scheduled)
        for r in scheduled:
            types = set(r.get("matchUpdateTypes", []))
            self.assertTrue(types, r["description"])
            self.assertNotIn("major", types, r["description"])
            # cloud-infra's app image bumps (staging automerge, prod PRs) are the release flow: never scheduled
            self.assertLessEqual(ORG, set(r.get("matchPackageNames", [])), r["description"])

    def test_security_fixes_keep_renovates_unscheduled_default(self):
        for name in ("default.json", "app-stack.json"):
            self.assertNotIn("schedule", load(name).get("vulnerabilityAlerts", {}), name)

    def test_shared_docker_group_leaves_org_images_alone(self):
        docker = [r for r in self.rules() if r.get("groupName") == "docker non-major"]
        self.assertEqual(len(docker), 1)
        self.assertIn("!ghcr.io/fjcloudaiconsulting/**", docker[0]["matchPackageNames"])

    def test_actions_group_leaves_version_inputs_alone(self):
        # setup-terraform's terraform_version (depType uses-with) stays in cloud-infra's terraform monorepo group
        actions = [r for r in self.rules() if r.get("groupName") == "github-actions non-major"]
        self.assertEqual(len(actions), 1)
        self.assertEqual(set(actions[0]["matchDepTypes"]), {"action", "workflow"})


if __name__ == "__main__":
    unittest.main()
