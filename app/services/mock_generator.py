"""Deterministic mock alert data generator for ThreatFlow ingestion replay testing."""
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.schemas.alert import AlertCreate


def get_mock_replay_sequence() -> List[Dict[str, Any]]:
    """
    Returns deterministic mock alert payloads for testing out-of-order, duplicate,
    conflicting, and incomplete alert ingestion.

    Delivery Order:
    1. SOURCE_B alert  (EVENT 1, event_timestamp=10:00:05Z)
    2. SOURCE_C alert  (EVENT 1, event_timestamp=10:00:15Z, incomplete evidence)
    3. SOURCE_B dup    (EVENT 1, event_timestamp=10:00:05Z, duplicate alert_id B-2001)
    4. SOURCE_A alert  (EVENT 1, event_timestamp=09:10:05Z, older event delivered out-of-order)
    5. EVENT 2 alert   (EVENT 2, transaction_id=TX-9999, same account C-101, different transaction)
    """
    return [
        {
            "alert_id": "B-2001",
            "external_alert_id": "B-2001",
            "source": "SOURCE_B",
            "transaction_id": "TX-5001",
            "account_id": "C-101",
            "event_timestamp": "2026-08-23T10:00:05Z",
            "amount": 15000.0,
            "merchant": "Amazon",
            "location": "Singapore",  # Conflicting location with SOURCE_A (Chennai)
            "device_id": "DEV-22",
            "risk_level": "MEDIUM",  # Conflicting risk level with SOURCE_A (HIGH)
            "fraud_type": "ONLINE_CARD",
            "metadata": {"feed_id": "feed-b-v1"},
        },
        {
            "alert_id": "C-3001",
            "external_alert_id": "C-3001",
            "source": "SOURCE_C",
            "transaction_id": "TX-5001",
            "account_id": "C-101",
            "event_timestamp": "2026-08-23T10:00:15Z",
            "amount": 18000.0,
            "merchant": None,  # Incomplete evidence
            "location": "Chennai",
            "device_id": None,  # Incomplete evidence
            "risk_level": "CRITICAL",
            "fraud_type": "ACCOUNT_TAKEOVER",
            "metadata": {"feed_id": "feed-c-v2"},
        },
        {
            "alert_id": "B-2001",  # Duplicate alert_id B-2001
            "external_alert_id": "B-2001",
            "source": "SOURCE_B",
            "transaction_id": "TX-5001",
            "account_id": "C-101",
            "event_timestamp": "2026-08-23T10:00:05Z",
            "amount": 15000.0,
            "merchant": "Amazon",
            "location": "Singapore",
            "device_id": "DEV-22",
            "risk_level": "MEDIUM",
            "fraud_type": "ONLINE_CARD",
            "metadata": {"feed_id": "feed-b-v1", "retransmitted": True},
        },
        {
            "alert_id": "A-1001",
            "external_alert_id": "A-1001",
            "source": "SOURCE_A",
            "transaction_id": "TX-5001",
            "account_id": "C-101",
            "event_timestamp": "2026-08-23T09:10:05Z",  # Older timestamp arriving late!
            "amount": 15000.0,
            "merchant": "Amazon",
            "location": "Chennai",
            "device_id": "DEV-22",
            "risk_level": "HIGH",
            "fraud_type": "POS_FRAUD",
            "metadata": {"feed_id": "feed-a-v1"},
        },
        {
            "alert_id": "A-9001",  # EVENT 2: Different transaction on same account
            "external_alert_id": "A-9001",
            "source": "SOURCE_A",
            "transaction_id": "TX-9999",  # Different transaction!
            "account_id": "C-101",
            "event_timestamp": "2026-08-23T10:05:00Z",
            "amount": 500.0,
            "merchant": "Swiggy",
            "location": "Mumbai",
            "device_id": "DEV-55",
            "risk_level": "LOW",
            "fraud_type": "LOW_RISK_TX",
            "metadata": {"feed_id": "feed-a-v1"},
        },
    ]


def get_scenario_alerts(scenario: str) -> List[Dict[str, Any]]:
    """Returns mock alerts for specific scenario names."""
    scenarios_map = {
        "normal": [
            {
                "alert_id": "A-1001",
                "source": "SOURCE_A",
                "transaction_id": "TX-1001",
                "account_id": "C-101",
                "event_timestamp": "2026-08-23T09:10:00Z",
                "amount": 500.0,
                "merchant": "Walmart",
                "location": "Chennai",
                "device_id": "DEV-01",
                "risk_level": "LOW",
            }
        ],
        "duplicate": [
            {
                "alert_id": "A-1002",
                "source": "SOURCE_A",
                "transaction_id": "TX-1002",
                "account_id": "C-102",
                "event_timestamp": "2026-08-23T09:15:00Z",
                "amount": 12000.0,
                "location": "Mumbai",
                "risk_level": "HIGH",
            },
            {
                "alert_id": "A-1002",  # Duplicate
                "source": "SOURCE_A",
                "transaction_id": "TX-1002",
                "account_id": "C-102",
                "event_timestamp": "2026-08-23T09:15:00Z",
                "amount": 12000.0,
                "location": "Mumbai",
                "risk_level": "HIGH",
            },
        ],
        "conflict": [
            {
                "alert_id": "A-1003",
                "source": "SOURCE_A",
                "transaction_id": "TX-1003",
                "account_id": "C-103",
                "event_timestamp": "2026-08-23T09:20:00Z",
                "amount": 15000.0,
                "location": "Chennai",
                "risk_level": "HIGH",
            },
            {
                "alert_id": "B-1003",
                "source": "SOURCE_B",
                "transaction_id": "TX-1003",
                "account_id": "C-103",
                "event_timestamp": "2026-08-23T09:20:02Z",
                "amount": 15000.0,
                "location": "Singapore",  # Conflicting location
                "risk_level": "MEDIUM",  # Conflicting risk level
            },
            {
                "alert_id": "C-1003",
                "source": "SOURCE_C",
                "transaction_id": "TX-1003",
                "account_id": "C-103",
                "event_timestamp": "2026-08-23T09:20:05Z",
                "amount": 18000.0,  # Conflicting amount
                "location": "Chennai",
                "risk_level": "CRITICAL",
            },
        ],
        "incomplete": [
            {
                "alert_id": "A-1004",
                "source": "SOURCE_A",
                "transaction_id": "TX-1004",
                "account_id": "C-104",
                "event_timestamp": "2026-08-23T09:20:00Z",
                "amount": 20000.0,
                "merchant": None,
                "location": None,
                "device_id": "DEV-22",
                "risk_level": None,
            }
        ],
        "out_of_order": [
            {
                "alert_id": "A-1005",
                "source": "SOURCE_A",
                "transaction_id": "TX-1005",
                "account_id": "C-105",
                "event_timestamp": "2026-08-23T10:00:10Z",
                "amount": 1000.0,
                "risk_level": "MEDIUM",
            },
            {
                "alert_id": "C-1005",
                "source": "SOURCE_C",
                "transaction_id": "TX-1005",
                "account_id": "C-105",
                "event_timestamp": "2026-08-23T10:00:15Z",
                "amount": 1000.0,
                "risk_level": "HIGH",
            },
            {
                "alert_id": "B-1005",
                "source": "SOURCE_B",
                "transaction_id": "TX-1005",
                "account_id": "C-105",
                "event_timestamp": "2026-08-23T10:00:05Z",  # Arrives 3rd, but has oldest event_timestamp!
                "amount": 1000.0,
                "risk_level": "LOW",
            },
        ],
    }
    return scenarios_map.get(scenario.lower(), get_mock_replay_sequence())
