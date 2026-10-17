"""Workflow: the state of local resources and SSM tunnels.

The four Workflow screens read from here and nowhere else — Kafka local, cluster
tunnels, services with their own destination, and the database. Everything under
this package is read-only for now: no route may start or stop anything through it
until the readiness probes land, because "up" without a probe is the one answer
these screens must never give.
"""