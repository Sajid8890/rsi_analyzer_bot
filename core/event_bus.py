import queue
import threading

class EventBus:
    """A simple thread-safe event bus using a queue."""
    def __init__(self):
        self.event_queue = queue.Queue()
        self.subscribers = {}
        self.lock = threading.Lock()

    def subscribe(self, event_type, callback):
        """Subscribe a callback to a specific event type."""
        with self.lock:
            if event_type not in self.subscribers:
                self.subscribers[event_type] = []
            self.subscribers[event_type].append(callback)

    def publish(self, event_type, data=None):
        """Publish an event to the bus."""
        event = {'type': event_type, 'data': data}
        self.event_queue.put(event)

    def process_events(self):
        """Continuously process events from the queue and dispatch to subscribers."""
        while True:
            try:
                event = self.event_queue.get()
                event_type = event.get('type')
                with self.lock:
                    if event_type in self.subscribers:
                        for callback in self.subscribers[event_type]:
                            try:
                                # Run callback in a new thread to avoid blocking the bus
                                threading.Thread(target=callback, args=(event['data'],)).start()
                            except Exception as e:
                                print(f"🚨 Error executing callback for event {event_type}: {e}")
            except queue.Empty:
                continue # Should not happen with a blocking get()
            except Exception as e:
                print(f"🚨 Critical error in event bus processing loop: {e}")
