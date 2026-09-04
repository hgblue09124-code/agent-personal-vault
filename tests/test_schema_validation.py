import pytest
from pathlib import Path
from agent_personal_vault.validation import SchemaValidator
from agent_personal_vault.models import Envelope
from jsonschema import ValidationError


def test_schema_validation_valid_entity():
    validator = SchemaValidator()
    env = Envelope(
        id="test-id-1",
        type="identity",
        data={
            "display_name": "Test User",
            "handle": "test_user",
            "timezone": "UTC"
        }
    )
    # Should pass without exception
    validator.validate_entity(env.to_dict())


def test_schema_validation_invalid_domain_data():
    validator = SchemaValidator()
    env = Envelope(
        id="test-id-2",
        type="identity",
        data={
            # Missing required field "display_name"
            "handle": "test_user"
        }
    )
    with pytest.raises(ValidationError):
        validator.validate_entity(env.to_dict())


def test_schema_validation_invalid_envelope():
    validator = SchemaValidator()
    invalid_env = {
        # Missing schema_version and id
        "type": "identity",
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
        "data": {"display_name": "Test"}
    }
    with pytest.raises(ValidationError):
        validator.validate_entity(invalid_env)
