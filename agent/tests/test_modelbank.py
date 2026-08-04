"""Model-folder tests: dropping a file in model/ is how you choose a model.

All headless -- modelbank is pure parsing/selection plus plain file reads.
"""
import pytest
from secdogie_agent import modelbank


def write(directory, name, text=""):
    p = directory / name
    p.write_text(text, encoding="utf-8")
    return p


# -- parsing: every shape a user might reasonably produce ----------------------

def test_an_empty_file_named_after_a_model_is_that_model():
    # `touch model/claude-opus-4-8` -- the least ceremony possible.
    assert modelbank.parse_preset("claude-opus-4-8", "").model == "claude-opus-4-8"


def test_an_empty_file_with_a_nickname_names_no_model():
    # "fast" is not a model id; inventing one would fail at the API with a
    # baffling error. No model means the normal default still applies.
    assert modelbank.parse_preset("fast", "").model is None


def test_a_bare_model_id_in_the_file_wins():
    assert modelbank.parse_preset("fast", "claude-haiku-4-5\n").model == "claude-haiku-4-5"


def test_a_dotenv_line_is_understood():
    p = modelbank.parse_preset("careful", "SECDOGIE_MODEL=claude-opus-4-8\n")
    assert p.model == "claude-opus-4-8"


def test_comments_and_blank_lines_are_skipped():
    p = modelbank.parse_preset("x", "\n# a note\n\nclaude-sonnet-5\n")
    assert p.model == "claude-sonnet-5"


def test_an_explicit_key_beats_a_bare_line():
    p = modelbank.parse_preset("x", "gpt-5.5\nSECDOGIE_MODEL=claude-sonnet-5\n")
    assert p.model == "claude-sonnet-5"


def test_a_preset_can_carry_its_providers_api_key():
    p = modelbank.parse_preset("openai", "SECDOGIE_MODEL=gpt-5.5\nOPENAI_API_KEY=sk-test\n")
    assert p.model == "gpt-5.5"
    assert p.api_keys == {"OPENAI_API_KEY": "sk-test"}


def test_unrelated_keys_are_not_reported_as_api_keys():
    p = modelbank.parse_preset("x", "SECDOGIE_MODEL=claude-sonnet-5\nNOTE=hello\n")
    assert p.api_keys == {}


def test_a_provider_ref_is_recognized_as_a_model_id():
    assert modelbank.looks_like_model_id("openai/gpt-5.5")
    assert not modelbank.looks_like_model_id("fast")


# -- which files count ---------------------------------------------------------

@pytest.mark.parametrize("name", ["README.md", ".DS_Store", "_example.txt", "readme"])
def test_notes_and_hidden_files_are_not_presets(name):
    assert not modelbank.is_preset_file(name)


@pytest.mark.parametrize("name", ["claude-opus-4-8", "fast.txt", "openai.env"])
def test_ordinary_files_are_presets(name):
    assert modelbank.is_preset_file(name)


def test_the_extension_is_not_part_of_the_name():
    assert modelbank.preset_name("fast.txt") == "fast"
    assert modelbank.preset_name("claude-opus-4-8") == "claude-opus-4-8"


# -- selection -----------------------------------------------------------------

def test_a_single_preset_is_simply_used():
    presets = [modelbank.parse_preset("fast", "claude-haiku-4-5")]
    assert modelbank.pick(presets).model == "claude-haiku-4-5"


def test_no_presets_means_no_opinion():
    assert modelbank.pick([]) is None


def test_several_presets_with_no_choice_refuses_to_guess():
    presets = [
        modelbank.parse_preset("careful", "claude-opus-4-8"),
        modelbank.parse_preset("fast", "claude-haiku-4-5"),
    ]
    with pytest.raises(modelbank.AmbiguousModel) as e:
        modelbank.pick(presets)
    assert set(e.value.names) == {"careful", "fast"}


def test_a_preset_named_default_breaks_the_tie():
    presets = [
        modelbank.parse_preset("default", "claude-opus-4-8"),
        modelbank.parse_preset("fast", "claude-haiku-4-5"),
    ]
    assert modelbank.pick(presets).model == "claude-opus-4-8"


def test_a_request_matches_the_preset_name_first():
    presets = [modelbank.parse_preset("fast", "claude-haiku-4-5")]
    assert modelbank.pick(presets, "fast").model == "claude-haiku-4-5"


def test_a_request_also_matches_the_model_id():
    # So --model claude-haiku-4-5 still finds the file, and picks up its key.
    presets = [modelbank.parse_preset("fast", "SECDOGIE_MODEL=claude-haiku-4-5\nANTHROPIC_API_KEY=k")]
    assert modelbank.pick(presets, "claude-haiku-4-5").api_keys == {"ANTHROPIC_API_KEY": "k"}


def test_a_request_matching_nothing_is_not_a_match():
    presets = [modelbank.parse_preset("fast", "claude-haiku-4-5")]
    assert modelbank.pick(presets, "claude-opus-4-8") is None


def test_a_preset_with_no_model_never_makes_the_folder_ambiguous():
    # The shipped template, or a key-only file, must not turn one real choice
    # into a question.
    presets = [
        modelbank.parse_preset("keys-only", "ANTHROPIC_API_KEY=k"),
        modelbank.parse_preset("fast", "claude-haiku-4-5"),
    ]
    assert modelbank.pick(presets).name == "fast"


# -- the folder ----------------------------------------------------------------

def test_load_dir_reads_and_sorts(tmp_path):
    write(tmp_path, "fast.txt", "claude-haiku-4-5")
    write(tmp_path, "careful.txt", "claude-opus-4-8")
    write(tmp_path, "README.md", "notes")
    assert modelbank.names(modelbank.load_dir(tmp_path)) == ["careful", "fast"]


def test_load_dir_skips_subdirectories(tmp_path):
    (tmp_path / "sub").mkdir()
    write(tmp_path, "fast.txt", "claude-haiku-4-5")
    assert modelbank.names(modelbank.load_dir(tmp_path)) == ["fast"]


def test_a_missing_folder_is_empty_not_an_error():
    assert modelbank.load_dir(None) == []


def test_candidate_dirs_prefer_the_explicit_then_the_exe(tmp_path):
    dirs = modelbank.candidate_dirs("/somewhere/else", exe_dir=tmp_path)
    assert dirs[0].name == "else"
    assert dirs[1] == tmp_path / "model"


def test_find_dir_returns_the_first_that_exists(tmp_path):
    (tmp_path / "model").mkdir()
    assert modelbank.find_dir(exe_dir=tmp_path) == tmp_path / "model"


def test_the_created_folder_holds_no_presets_of_its_own(tmp_path):
    # Fresh from --init-model-dir: a README and a template, nothing that would
    # be mistaken for a choice the user made.
    d = modelbank.write_example_dir(tmp_path / "model")
    assert modelbank.usable(modelbank.load_dir(d)) == []
    assert modelbank.pick(modelbank.load_dir(d)) is None


def test_write_example_dir_never_clobbers(tmp_path):
    d = tmp_path / "model"
    modelbank.write_example_dir(d)
    (d / "README.md").write_text("mine", encoding="utf-8")
    modelbank.write_example_dir(d)
    assert (d / "README.md").read_text() == "mine"


def test_one_file_in_the_created_folder_is_all_it_takes(tmp_path):
    d = modelbank.write_example_dir(tmp_path / "model")
    write(d, "claude-opus-4-8")
    assert modelbank.pick(modelbank.load_dir(d)).model == "claude-opus-4-8"
