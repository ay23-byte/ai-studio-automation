"""A deterministic, curated weekly documentary topic rotation.

The queue keeps scheduled builds independent of manual workflow inputs and avoids
repeating a topic for a full ISO-week cycle. Topics are broad research prompts;
the documentary's normal source, visual, rights and release gates still apply.
"""
from __future__ import annotations

from datetime import date

TOPICS = (
    "How India's semiconductor fabs are built",
    "How nuclear reactors control chain reactions",
    "How high-voltage power grids move electricity",
    "The physics of superconductors and future power",
    "How GPS satellites locate a phone",
    "How quantum computers actually work",
    "Inside a modern lithium-ion battery",
    "How India's space programme launches satellites",
    "Why fusion energy is so difficult",
    "How undersea internet cables connect the world",
    "How earthquake-resistant buildings are engineered",
    "The hidden engineering behind bullet trains",
    "How a chip is manufactured from sand",
    "How wind turbines convert moving air to power",
    "The science of desalination at massive scale",
    "How rockets land vertically",
    "Why the James Webb telescope sees the early universe",
    "How nuclear submarines stay underwater",
    "The engineering behind India's UPI payments infrastructure",
    "How 5G networks transmit data",
    "The physics of black holes",
    "How carbon capture works",
    "How electric vehicle motors work",
    "The engineering of mega dams",
    "How satellites monitor climate change",
    "The science behind medical MRI scanners",
    "How aviation jet engines work",
    "Why the world's largest bridges do not collapse",
    "How 3D printing is changing manufacturing",
    "How data centres are engineered to stay cool",
    "How batteries store renewable energy",
    "How semiconductor lithography patterns nanometres",
    "How the internet routes a data packet",
    "The physics of high-speed trains",
    "How India's nuclear energy programme works",
    "How the International Space Station recycles air and water",
    "How fibre optics carry information",
    "How a modern wind farm connects to the grid",
    "The science of earthquake early warning",
    "How autonomous cars perceive the road",
    "How large language models run on GPUs",
    "How satellites stay in orbit",
    "The engineering behind hydroelectric power",
    "How heat pumps move thermal energy",
    "The physics of magnetic levitation",
    "How a modern aircraft is built",
    "How desalination plants turn seawater into drinking water",
    "How precision clocks keep GPS accurate",
    "How the world's largest cargo ships are powered",
    "How nuclear fusion magnets confine plasma",
    "How microprocessors execute instructions",
    "How India's high-speed rail corridors are engineered",
)


def scheduled_topic(today: date | None = None) -> str:
    """Return this ISO week's documentary topic, stable across reruns that week."""
    today = today or date.today()
    index = (today.isocalendar().week - 1) % len(TOPICS)
    return TOPICS[index]
