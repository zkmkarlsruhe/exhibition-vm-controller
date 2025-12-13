"""
Exhibition VM Controller - Framework for conservation of digital artworks.

Author: Marc Schütze
Organization: ZKM | Center for Art and Media Karlsruhe
Contact: mschuetze@zkm.de
License: MIT

This framework provides tools for preserving and exhibiting historical digital
artworks using virtual machines with snapshot-based error recovery.
"""

__version__ = "1.2.0"
__author__ = "Marc Schütze"
__email__ = "mschuetze@zkm.de"
__organization__ = "ZKM | Center for Art and Media Karlsruhe"

from vm_controller.vm_manager import VMManager
from vm_controller.heartbeat_monitor import HeartbeatMonitor
from vm_controller.config import Config

__all__ = ["VMManager", "HeartbeatMonitor", "Config"]
