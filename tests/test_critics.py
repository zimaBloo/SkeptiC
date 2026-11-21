from skeptic.critics import (
    CRITICS,
    TITLES,
    critic_system_prompt,
    load_prompt,
    prompts_digest,
    submission_prompt,
)


def test_every_critic_has_a_title_and_a_role_prompt() -> None:
    assert set(TITLES) == set(CRITICS)
    for critic in CRITICS:
        assert load_prompt(f"roles/{critic}.md").strip()


def test_system_prompt_combines_shared_rules_and_own_focus() -> None:
    for critic in CRITICS:
        prompt = critic_system_prompt(critic)

        assert prompt.startswith(f"You are the {TITLES[critic]} critic")
        assert load_prompt(f"roles/{critic}.md").strip() in prompt
        assert "## Severity of an issue" in prompt
        assert "$" not in prompt


def test_each_critic_gets_a_different_focus() -> None:
    prompts = {critic_system_prompt(critic) for critic in CRITICS}

    assert len(prompts) == len(CRITICS)


def test_prompt_names_every_field_of_the_verdict() -> None:
    prompt = critic_system_prompt("market")

    for field in ("summary", "issues", "quote", "why_it_matters", "how_to_improve", "overall"):
        assert field in prompt


def test_submission_holds_the_idea_as_written() -> None:
    idea = "A $5 app.\n\nIt has {braces} and $dollar signs."

    assert submission_prompt(idea).endswith(idea + "\n")


def test_prompts_digest_is_a_stable_sha256() -> None:
    digest = prompts_digest()

    assert len(digest) == 64
    assert digest == prompts_digest()


def test_prompt_lists_the_answer_fields_in_schema_order() -> None:
    prompt = critic_system_prompt("risk")
    answer = prompt[prompt.index("## Answer") :]

    positions = [answer.index(f"- {field}:") for field in ("issues", "summary", "overall")]
    assert positions == sorted(positions)
