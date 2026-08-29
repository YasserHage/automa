"""Model routing and credential resolution."""

from __future__ import annotations

import pytest

from automa import config
from automa.config import MissingCredentialError


class TestModelForRole:
    def test_every_role_resolves_to_a_provider_prefixed_model(self) -> None:
        for role in config.DEFAULT_MODELS:
            assert ":" in config.model_for(role)

    def test_per_role_env_var_overrides_the_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTOMA_MODEL_TACTICIAN", "anthropic:claude-haiku-4-5")
        assert config.model_for("tactician") == "anthropic:claude-haiku-4-5"
        assert config.model_for("compiler") == config.DEFAULT_MODELS["compiler"]

    def test_global_env_var_moves_every_role_at_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Moving the whole system to another model must not need a code change."""
        monkeypatch.setenv("AUTOMA_MODEL", "anthropic:claude-sonnet-5")
        for role in config.DEFAULT_MODELS:
            assert config.model_for(role) == "anthropic:claude-sonnet-5"

    def test_a_single_role_can_be_lifted_out_of_the_global_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The specific override wins, so the Compiler can differ from the Tactician."""
        monkeypatch.setenv("AUTOMA_MODEL", "anthropic:claude-sonnet-5")
        monkeypatch.setenv("AUTOMA_MODEL_COMPILER", "anthropic:claude-opus-5")
        assert config.model_for("compiler") == "anthropic:claude-opus-5"
        assert config.model_for("tactician") == "anthropic:claude-sonnet-5"

    def test_an_empty_override_falls_through_instead_of_blanking_the_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`AUTOMA_MODEL=` in a .env file is a common way to mean "unset"."""
        monkeypatch.setenv("AUTOMA_MODEL", "")
        monkeypatch.setenv("AUTOMA_MODEL_TACTICIAN", "")
        assert config.model_for("tactician") == config.DEFAULT_MODELS["tactician"]

    def test_every_role_defaults_to_the_same_model(self) -> None:
        """Phase 3's comparison only tests the architecture if both arms match."""
        assert set(config.DEFAULT_MODELS.values()) == {config.DEFAULT_MODEL}

    def test_every_default_model_has_a_registered_price(self) -> None:
        """An unpriced default would report cost as None for every single turn,
        quietly making the cost-per-turn metric useless."""
        from automa.llm.pricing import PRICES

        for role, model in config.DEFAULT_MODELS.items():
            assert model in PRICES, f"role {role!r} defaults to unpriced model {model!r}"

    def test_baseline_and_tactician_share_a_model(self) -> None:
        """Phase 3 requires a fair baseline. Running both arms on the same model
        means the comparison tests the architecture, not the model."""
        assert config.DEFAULT_MODELS["baseline"] == config.DEFAULT_MODELS["tactician"]

    def test_unknown_role_names_the_known_ones(self) -> None:
        with pytest.raises(ValueError, match="tactician"):
            config.model_for("umpire")

    def test_an_unknown_role_is_not_rescued_by_the_global_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A typo in a role name must read as a typo, not silently resolve."""
        monkeypatch.setenv("AUTOMA_MODEL", "anthropic:claude-sonnet-5")
        with pytest.raises(ValueError, match="umpire"):
            config.model_for("umpire")


class TestCredentials:
    def test_each_provider_has_its_own_variable(self) -> None:
        """A shared key could not support two providers running in one match."""
        variables = set(config.PROVIDER_CREDENTIALS.values())
        assert len(variables) == len(config.PROVIDER_CREDENTIALS)

    def test_missing_key_names_the_variable_and_the_file(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(MissingCredentialError) as exc:
            config.require_credentials("anthropic")
        message = str(exc.value)
        assert "ANTHROPIC_API_KEY" in message
        assert ".env" in message

    def test_present_key_passes(self) -> None:
        config.require_credentials("anthropic")  # placeholder set by conftest

    def test_unknown_provider_is_left_to_the_resolver(self) -> None:
        config.require_credentials("nonesuch")  # must not raise here
