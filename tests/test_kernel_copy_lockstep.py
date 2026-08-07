"""KERNEL-COPY-LOCKSTEP-UNGATED (S187 opened, S188 gated).

The project runs its kernel from ``RAG/rag_kernel`` and tests it in the git worktree
named by ``meta.reconciliation_docs_root``. Through S187 the two were kept identical
BY HAND with no invariant, which means "the code that passed 2,409 tests is the code
enforcing the rules" was a habit, not a fact. The S188 audit found them identical —
luck, not a guarantee. These tests pin the guarantee.

The three failure modes are distinct and each is worth its own message:
deployed-only (running code no test has seen), tested-only (a tested capability that
is not actually deployed), and differing content (the silent one).
"""

from __future__ import annotations

import pytest

from rag_kernel.drift_audit import ERROR, check_kernel_copy_lockstep


def _trees(tmp_path, deployed: dict, tested: dict, declared="repo"):
    rag_dir = tmp_path / "RAG"
    (rag_dir / "rag_kernel").mkdir(parents=True)
    repo = tmp_path / declared
    (repo / "rag_kernel").mkdir(parents=True)
    for name, body in deployed.items():
        p = rag_dir / "rag_kernel" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    for name, body in tested.items():
        p = repo / "rag_kernel" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    hot = {"meta": {"reconciliation_docs_root": declared}, "tracked_items": []}
    return hot, tmp_path, rag_dir


class TestLockstep:
    def test_identical_trees_are_clean(self, tmp_path):
        hot, root, rag_dir = _trees(
            tmp_path, {"a.py": "x = 1\n"}, {"a.py": "x = 1\n"}
        )
        assert check_kernel_copy_lockstep(hot, root, rag_dir) == []

    def test_differing_content_is_an_error(self, tmp_path):
        hot, root, rag_dir = _trees(
            tmp_path, {"a.py": "x = 1\n"}, {"a.py": "x = 2\n"}
        )
        f = check_kernel_copy_lockstep(hot, root, rag_dir)
        assert len(f) == 1
        assert f[0].severity == ERROR
        assert f[0].check == "kernel_copy_lockstep"
        assert "tested code is not running code" in f[0].detail

    def test_deployed_only_module_is_an_error(self, tmp_path):
        hot, root, rag_dir = _trees(
            tmp_path, {"a.py": "x\n", "secret.py": "y\n"}, {"a.py": "x\n"}
        )
        f = check_kernel_copy_lockstep(hot, root, rag_dir)
        assert len(f) == 1
        assert "no test has seen" in f[0].detail

    def test_tested_only_module_is_an_error(self, tmp_path):
        hot, root, rag_dir = _trees(
            tmp_path, {"a.py": "x\n"}, {"a.py": "x\n", "new.py": "z\n"}
        )
        f = check_kernel_copy_lockstep(hot, root, rag_dir)
        assert len(f) == 1
        assert "not actually deployed" in f[0].detail

    def test_nested_packages_are_compared(self, tmp_path):
        hot, root, rag_dir = _trees(
            tmp_path, {"sub/a.py": "x = 1\n"}, {"sub/a.py": "x = 9\n"}
        )
        assert len(check_kernel_copy_lockstep(hot, root, rag_dir)) == 1

    def test_pycache_is_ignored(self, tmp_path):
        """Bytecode differs legitimately by interpreter; it is a build artifact."""
        hot, root, rag_dir = _trees(
            tmp_path,
            {"a.py": "x\n", "__pycache__/a.cpython-312.pyc": "AAA"},
            {"a.py": "x\n", "__pycache__/a.cpython-313.pyc": "BBB"},
        )
        assert check_kernel_copy_lockstep(hot, root, rag_dir) == []

    def test_every_divergent_module_is_reported_not_just_the_first(self, tmp_path):
        hot, root, rag_dir = _trees(
            tmp_path,
            {"a.py": "1\n", "b.py": "1\n", "c.py": "1\n"},
            {"a.py": "2\n", "b.py": "2\n", "c.py": "1\n"},
        )
        assert len(check_kernel_copy_lockstep(hot, root, rag_dir)) == 2


class TestSelfSkip:
    def test_no_declared_docs_root_skips(self, tmp_path):
        hot, root, rag_dir = _trees(tmp_path, {"a.py": "1\n"}, {"a.py": "2\n"})
        hot["meta"] = {}
        assert check_kernel_copy_lockstep(hot, root, rag_dir) == []

    def test_absent_tested_tree_skips(self, tmp_path):
        hot, root, rag_dir = _trees(tmp_path, {"a.py": "1\n"}, {"a.py": "2\n"})
        hot["meta"]["reconciliation_docs_root"] = "somewhere-else"
        assert check_kernel_copy_lockstep(hot, root, rag_dir) == []

    def test_no_root_skips(self, tmp_path):
        hot, root, rag_dir = _trees(tmp_path, {"a.py": "1\n"}, {"a.py": "2\n"})
        assert check_kernel_copy_lockstep(hot, None, rag_dir) == []

    def test_no_rag_dir_skips(self, tmp_path):
        hot, root, rag_dir = _trees(tmp_path, {"a.py": "1\n"}, {"a.py": "2\n"})
        assert check_kernel_copy_lockstep(hot, root, None) == []
