import threading

class BaseService(threading.Thread):
    """Base class for all services, ensuring they run in a separate thread."""
    def __init__(self, state_manager, event_bus):
        super().__init__()
        self.daemon = True  # Allows main thread to exit even if services are running
        self.state = state_manager
        self.event_bus = event_bus

    def run(self):
        """The main loop for the service. To be implemented by subclasses."""
        raise NotImplementedError
