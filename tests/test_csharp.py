from pathlib import Path

from mutator.functions import is_private
from mutator import runner
from mutator.sites import apply_site, discover_raw
from mutator.workers import SKIP_LINK, create_workers, delete_tree, new_run_dir

SOURCE = """\
class Board {
    public int Score(int v, bool a, Board b) {
        if (v > 0 && !a) return 1;
        return b?.Size ?? -1;
    }
}
"""


def _sites(source: str):
    return [(site.original, site.mutant) for site in discover_raw(source, "csharp", "Board.cs")]


def test_csharp_uses_the_java_set_and_drops_the_conditional_access():
    assert _sites(SOURCE) == [
        (">", ">="),
        ("0", "1"),
        ("&&", "||"),
        ("!", ""),
        ("1", "0"),
        ("?", ""),
        ("-", ""),
        ("1", "0"),
    ]


def test_dropping_the_question_mark_leaves_plain_member_access():
    site = next(site for site in discover_raw(SOURCE, "csharp", "Board.cs") if site.original == "?")
    assert "b.Size ?? -1" in apply_site(SOURCE, site.start, site.end, site.mutant)


def test_ternary_and_nullable_question_marks_are_not_sites():
    source = "class B { int? F(bool a) { return a ? 2 : 3; } }\n"
    assert _sites(source) == []


def test_members_are_private_unless_declared_otherwise():
    source = """\
class Board {
    [Obsolete("x")]
    public int Shown() => 1;
    int Hidden() => 2;
    protected internal int Family() => 3;
    public int Size { get { return 1; } private set { } }
}
"""
    assert not is_private("csharp", "Shown", source, 2, 3)
    assert is_private("csharp", "Hidden", source, 4, 4)
    assert not is_private("csharp", "Family", source, 5, 5)


def test_explicit_interface_implementations_are_public():
    assert not is_private("csharp", "IStorage.Load", "int IStorage.Load(int id) => 1;\n", 1, 1)


def test_accessors_are_private_only_when_marked():
    assert not is_private("csharp", "Size.get", "get { return 1; }\n", 1, 1)
    assert is_private("csharp", "Size.set", "private set { }\n", 1, 1)


def test_plan_runs_dotnet_test_in_the_nearest_solution_with_private_artifacts(tmp_path):
    (tmp_path / "Demo.sln").write_text("", encoding="utf-8")
    project = tmp_path / "src" / "Demo"
    project.mkdir(parents=True)
    (project / "Demo.csproj").write_text("<Project />", encoding="utf-8")
    source = project / "Board.cs"
    source.write_text("class Board {}", encoding="utf-8")
    command, directory = runner.test_plan(tmp_path, source, "csharp", None)
    assert command[:2] == ["dotnet", "test"]
    assert command[command.index("--artifacts-path") + 1] == "target/dotnet-artifacts"
    assert directory == tmp_path.resolve()


def test_plan_skips_no_mutate_tests_in_xunit_and_mstest(tmp_path):
    (tmp_path / "Demo.slnx").write_text("<Solution />", encoding="utf-8")
    source = tmp_path / "Board.cs"
    source.write_text("class Board {}", encoding="utf-8")
    command, _directory = runner.test_plan(tmp_path, source, "csharp", None)
    # xUnit/NUnit traits are `Category`; MSTest's [TestCategory] is `TestCategory`.
    assert command[command.index("--filter") + 1] == "Category!=no-mutate&TestCategory!=no-mutate"


def test_plan_falls_back_to_the_project(tmp_path):
    project = tmp_path / "Demo"
    project.mkdir()
    (project / "Demo.csproj").write_text("<Project />", encoding="utf-8")
    source = project / "Board.cs"
    source.write_text("class Board {}", encoding="utf-8")
    _command, directory = runner.test_plan(tmp_path, source, "csharp", None)
    assert directory == project.resolve()


def test_workers_do_not_share_dotnet_build_output():
    assert {"bin", "obj", "TestResults"} <= SKIP_LINK


def test_workers_give_every_dotnet_project_a_real_directory(tmp_path):
    # Through a linked directory a test project's deps.json loses its project
    # references, and a Microsoft.Testing.Platform test app can't load them.
    (tmp_path / "Demo.slnx").write_text("<Solution />", encoding="utf-8")
    source = tmp_path / "src" / "Demo" / "Board.cs"
    source.parent.mkdir(parents=True)
    source.write_text("class Board {}", encoding="utf-8")
    (tmp_path / "src" / "Demo" / "Demo.csproj").write_text("<Project />", encoding="utf-8")
    shared = tmp_path / "src" / "Shared" / "Shared.csproj"
    shared.parent.mkdir()
    shared.write_text("<Project />", encoding="utf-8")
    tests = tmp_path / "tests" / "Demo.PropertiesTests" / "Demo.PropertiesTests.csproj"
    tests.parent.mkdir(parents=True)
    tests.write_text("<Project />", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    original = source.read_bytes()

    base = new_run_dir(tmp_path)
    try:
        (worker,) = create_workers(base, tmp_path, "src/Demo/Board.cs", original, 1)
        for directory in ("tests", "tests/Demo.PropertiesTests", "src/Shared"):
            assert (worker / directory).is_dir()
            assert not (worker / directory).is_symlink()
        assert (worker / "tests/Demo.PropertiesTests/Demo.PropertiesTests.csproj").is_symlink()
        assert (worker / "src/Shared/Shared.csproj").is_symlink()
        assert (worker / "docs").is_symlink()
    finally:
        delete_tree(base)
    assert tests.read_text(encoding="utf-8") == "<Project />"
