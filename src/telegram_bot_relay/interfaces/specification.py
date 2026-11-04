"""Schemas generated from the same contracts accepted by both interfaces."""

from typing import Any

from telegram_bot_relay.application.custom_emoji import InspectCustomEmoji, Inspection
from telegram_bot_relay.application.models import Outcome, operation_adapter


def openapi() -> dict[str, Any]:
    request = operation_adapter.json_schema(ref_template="#/components/schemas/{model}")
    definitions = request.pop("$defs", {})
    outcome = Outcome.model_json_schema(ref_template="#/components/schemas/{model}")
    definitions.update(outcome.pop("$defs", {}))
    definitions["Outcome"] = outcome
    inspection_request = InspectCustomEmoji.model_json_schema(
        ref_template="#/components/schemas/{model}"
    )
    definitions.update(inspection_request.pop("$defs", {}))
    inspection_response = Inspection.model_json_schema(ref_template="#/components/schemas/{model}")
    definitions.update(inspection_response.pop("$defs", {}))
    definitions["Inspection"] = inspection_response
    return {
        "openapi": "3.1.0",
        "info": {"title": "Telegram Bot Relay", "version": "0.4.0"},
        "security": [{"bearer": []}],
        "paths": {
            "/v1/custom-emoji": {
                "post": {
                    "operationId": "inspectCustomEmoji",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": inspection_request}},
                    },
                    "responses": {
                        "200": {
                            "description": "Read-only inspection; inspect status and missing IDs",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Inspection"}
                                }
                            },
                        },
                        "400": {"description": "Invalid inspection request"},
                        "401": {"description": "Unauthorized"},
                        "403": {"description": "Invalid origin"},
                        "413": {"description": "Request too large"},
                    },
                }
            },
            "/v1/operations": {
                "post": {
                    "operationId": "execute",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": request}},
                    },
                    "responses": {
                        "200": {
                            "description": "Operation outcome; inspect status",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Outcome"}
                                }
                            },
                        },
                        "400": {"description": "Invalid operation"},
                        "401": {"description": "Unauthorized"},
                        "403": {"description": "Invalid origin"},
                        "413": {"description": "Request too large"},
                    },
                }
            },
        },
        "components": {
            "schemas": definitions,
            "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
        },
    }
