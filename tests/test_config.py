"""Model routing and credential resolution."""

from __future__ import annotations

import pytest

from automa import config
from automa.config import MissingCredentialError


class TestModelForRole:
    def test_every_role_resolves_to_a_provider_prefixed_model(self) -> None:
        for role in config.DEFAULT_MODELS:
            assert ":" in config.model_for(role)

    def test_env_var_overrides_the_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOMA_MODEL_TACTICIAN", "google:gemini-2.5-flash")
        assert config.model_for("tactician") == "google:gemini-2.5-flash"

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
