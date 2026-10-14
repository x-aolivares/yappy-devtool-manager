"""Workflow/Recursos: the state of local resources and SSM tunnels.

The screen this backs reads from here and nowhere else. Everything under this
package is read-only for now: no route may start or stop anything through it
until the readiness probes land, because "up" without a probe is the one answer
this screen must never give.
"""