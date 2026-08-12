"""Small isolated smoke for the release-scale load generator."""

from scripts.pmo_loadgen import run_load


def test_loadgen_seeds_only_its_marker_verified_root(tmp_path):
    root = tmp_path / "loadgen"
    result = run_load(root, tickets=12, messages=25, repeats=1)

    assert result["schema"] == "datansh-pmo-loadgen-result.v1"
    assert result["ticket_count"] == 12
    assert result["largest_thread_messages"] == 25
    assert (root / ".datansh-pmo-loadgen.json").is_file()
    assert (root / "workspace" / ".datansh" / "project.yaml").is_file()
