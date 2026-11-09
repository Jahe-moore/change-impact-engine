"""Demo data: the IP-200 infusion pump, an invented medical device.

Nothing here describes a real company or product.
"""
from . import audit
from .db import tx

REQUIREMENTS = [
    ("REQ-001", "Deliver the set flow rate within 5%", "Systems Engineer"),
    ("REQ-002", "Detect a blocked IV line within 30 seconds", "Systems Engineer"),
    ("REQ-003", "Run 4 hours on battery", "Systems Engineer"),
    ("REQ-004", "Survive a 1 meter drop", "Mechanical Lead"),
    ("REQ-005", "Block doses above the drug library limit", "Software Lead"),
    ("REQ-010", "Check line pressure at least 10 times a second", "Firmware Lead"),
    ("REQ-011", "Blockage alarm loud enough to hear across a room", "Electrical Lead"),
    ("REQ-012", "Battery holds at least 2600 mAh", "Electrical Lead"),
]

PARTS = [
    ("PRT-100", "Whole pump", "Mechanical Lead"),
    ("PRT-110", "Pumping mechanism", "Mechanical Lead"),
    ("PRT-111", "Stepper motor", "Mechanical Lead"),
    ("PRT-112", "Roller cam", "Mechanical Lead"),
    ("PRT-120", "Control board", "Electrical Lead"),
    ("PRT-121", "Main chip", "Electrical Lead"),
    ("PRT-122", "Pressure sensor", "Electrical Lead"),
    ("PRT-123", "Alarm speaker", "Electrical Lead"),
    ("PRT-124", "Pump software, version 2.3", "Firmware Lead"),
    ("PRT-130", "Outer case", "Mechanical Lead"),
    ("PRT-140", "Battery pack", "Electrical Lead"),
]

TESTS = [
    ("TST-201", "Flow accuracy test", "Test Lead"),
    ("TST-202", "Blockage alarm test", "Test Lead"),
    ("TST-203", "Alarm loudness test", "Test Lead"),
    ("TST-204", "Battery life test", "Test Lead"),
    ("TST-205", "Drop test", "Test Lead"),
    ("TST-206", "Dose limit test", "Software Lead"),
    ("TST-207", "Full pump check", "Test Lead"),
]

LINKS = [
    # requirement derivation
    ("REQ-002", "REQ-010", "derives"), ("REQ-002", "REQ-011", "derives"), ("REQ-003", "REQ-012", "derives"),
    # BOM
    ("PRT-100", "PRT-110", "contains"), ("PRT-100", "PRT-120", "contains"),
    ("PRT-100", "PRT-130", "contains"), ("PRT-100", "PRT-140", "contains"),
    ("PRT-110", "PRT-111", "contains"), ("PRT-110", "PRT-112", "contains"),
    ("PRT-120", "PRT-121", "contains"), ("PRT-120", "PRT-122", "contains"),
    ("PRT-120", "PRT-123", "contains"), ("PRT-120", "PRT-124", "contains"),
    # requirement satisfied by part
    ("REQ-001", "PRT-111", "satisfied_by"), ("REQ-001", "PRT-112", "satisfied_by"), ("REQ-001", "PRT-124", "satisfied_by"),
    ("REQ-010", "PRT-122", "satisfied_by"), ("REQ-010", "PRT-124", "satisfied_by"),
    ("REQ-011", "PRT-123", "satisfied_by"), ("REQ-012", "PRT-140", "satisfied_by"),
    ("REQ-004", "PRT-130", "satisfied_by"), ("REQ-005", "PRT-124", "satisfied_by"),
    # requirement verified by test
    ("REQ-001", "TST-201", "verified_by"), ("REQ-002", "TST-202", "verified_by"), ("REQ-010", "TST-202", "verified_by"),
    ("REQ-011", "TST-203", "verified_by"), ("REQ-003", "TST-204", "verified_by"), ("REQ-012", "TST-204", "verified_by"),
    ("REQ-004", "TST-205", "verified_by"), ("REQ-005", "TST-206", "verified_by"),
    # test exercises part
    ("TST-201", "PRT-110", "exercises"), ("TST-202", "PRT-120", "exercises"), ("TST-203", "PRT-123", "exercises"),
    ("TST-204", "PRT-140", "exercises"), ("TST-205", "PRT-130", "exercises"), ("TST-206", "PRT-124", "exercises"),
    ("TST-207", "PRT-100", "exercises"),
]


def seed(conn) -> bool:
    """Load the demo product once. Returns False if data is already there."""
    if conn.execute("SELECT COUNT(*) FROM requirement").fetchone()[0]:
        return False
    with tx(conn):
        conn.executemany("INSERT INTO requirement (id, title, owner) VALUES (?,?,?)", REQUIREMENTS)
        conn.executemany("INSERT INTO part (id, title, owner) VALUES (?,?,?)", PARTS)
        conn.executemany("INSERT INTO test_case (id, title, owner) VALUES (?,?,?)", TESTS)
        conn.executemany("INSERT INTO trace_link (src, dst, link_type) VALUES (?,?,?)", LINKS)
        audit.record(conn, "system", "baseline.loaded", "IP-200",
                     {"requirements": len(REQUIREMENTS), "parts": len(PARTS), "tests": len(TESTS), "links": len(LINKS)})
    return True
