from __future__ import annotations

import unittest


class GraphTests(unittest.TestCase):
    def test_scope_normalization_is_idempotent_and_root_is_universal(self) -> None:
        from control_plane.scopes import (
            normalize_scope,
            scope_owns,
            scopes_overlap,
        )

        for raw, expected in (
            (".", "."),
            ("./", "."),
            ("./**", "."),
            ("src/", "src"),
            ("src/**", "src"),
        ):
            with self.subTest(raw=raw):
                normalized = normalize_scope(raw)
                self.assertEqual(normalized, expected)
                self.assertEqual(normalize_scope(normalized), normalized)

        self.assertTrue(scope_owns(".", "src/module.py"))
        self.assertTrue(scopes_overlap(".", "src/**"))
        self.assertTrue(scopes_overlap("src/**", "."))

    def test_independent_readers_join_is_valid(self) -> None:
        from control_plane.graph import validate_graph

        graph = {
            "schema_version": 1,
            "nodes": [
                {"id": "ios", "role": "reader", "allowed_paths": ["ios/**"], "depends_on": []},
                {"id": "api", "role": "reader", "allowed_paths": ["api/**"], "depends_on": []},
                {"id": "join", "role": "join", "allowed_paths": [], "depends_on": ["ios", "api"]},
            ],
        }

        self.assertEqual(validate_graph(graph, max_workers=2), [])

    def test_overlapping_writers_are_rejected(self) -> None:
        from control_plane.graph import validate_graph

        graph = {
            "schema_version": 1,
            "nodes": [
                {"id": "a", "role": "writer", "allowed_paths": ["src/auth/**"], "depends_on": []},
                {"id": "b", "role": "writer", "allowed_paths": ["src/**"], "depends_on": []},
            ]
        }

        codes = {issue.code for issue in validate_graph(graph, max_workers=2)}

        self.assertIn("G_WRITER_OVERLAP", codes)

    def test_repository_root_writer_overlaps_every_parallel_writer(self) -> None:
        from control_plane.graph import validate_graph

        for root_scope in (".", "./", "./**"):
            with self.subTest(root_scope=root_scope, order="root-first"):
                parallel = {
                    "schema_version": 1,
                    "nodes": [
                        {
                            "id": "root",
                            "role": "writer",
                            "allowed_paths": [root_scope],
                            "depends_on": [],
                        },
                        {
                            "id": "child",
                            "role": "writer",
                            "allowed_paths": ["src/**"],
                            "depends_on": [],
                        },
                    ],
                }
                self.assertIn(
                    "G_WRITER_OVERLAP",
                    {
                        issue.code
                        for issue in validate_graph(parallel, max_workers=2)
                    },
                )

            with self.subTest(root_scope=root_scope, order="child-first"):
                parallel["nodes"].reverse()
                self.assertIn(
                    "G_WRITER_OVERLAP",
                    {
                        issue.code
                        for issue in validate_graph(parallel, max_workers=2)
                    },
                )

            with self.subTest(root_scope=root_scope, mode="sequential"):
                sequential = {
                    "schema_version": 1,
                    "nodes": [
                        {
                            "id": "root",
                            "role": "writer",
                            "allowed_paths": [root_scope],
                            "depends_on": [],
                        },
                        {
                            "id": "child",
                            "role": "writer",
                            "allowed_paths": ["src/**"],
                            "depends_on": ["root"],
                        },
                    ],
                }
                self.assertNotIn(
                    "G_WRITER_OVERLAP",
                    {
                        issue.code
                        for issue in validate_graph(sequential, max_workers=1)
                    },
                )

    def test_sequential_chain_does_not_count_as_three_concurrent_workers(
        self,
    ) -> None:
        from control_plane.graph import validate_graph

        graph = {
            "schema_version": 1,
            "nodes": [
                {
                    "id": "one",
                    "role": "writer",
                    "allowed_paths": ["src/**"],
                    "depends_on": [],
                },
                {
                    "id": "two",
                    "role": "writer",
                    "allowed_paths": ["src/**"],
                    "depends_on": ["one"],
                },
                {
                    "id": "three",
                    "role": "writer",
                    "allowed_paths": ["src/**"],
                    "depends_on": ["two"],
                },
            ],
        }

        self.assertEqual(validate_graph(graph, max_workers=1), [])

    def test_cycles_traversal_missing_dependency_and_worker_limit_are_rejected(self) -> None:
        from control_plane.graph import validate_graph

        graph = {
            "schema_version": 1,
            "nodes": [
                {"id": "a", "role": "reader", "allowed_paths": ["../escape"], "depends_on": ["b"]},
                {"id": "b", "role": "reader", "allowed_paths": ["b/**"], "depends_on": ["a"]},
                {"id": "c", "role": "reader", "allowed_paths": ["c/**"], "depends_on": ["missing"]},
            ]
        }

        codes = {issue.code for issue in validate_graph(graph, max_workers=2)}

        self.assertTrue({"G_PATH", "G_CYCLE", "G_DEPENDENCY"}.issubset(codes))

        wide = {
            "schema_version": 1,
            "nodes": [
                {
                    "id": identifier,
                    "role": "reader",
                    "allowed_paths": [f"{identifier}/**"],
                    "depends_on": [],
                }
                for identifier in ("one", "two", "three")
            ],
        }
        self.assertIn(
            "G_WORKERS",
            {issue.code for issue in validate_graph(wide, max_workers=2)},
        )


if __name__ == "__main__":
    unittest.main()
