import logging
import threading

import datetime
import pytz
from kubernetes import client, config, watch


log = logging.getLogger(__name__)


class EventWatcher(threading.Thread):
    def __init__(self, namespace, *args, **kwargs):
        daemon = kwargs.pop("daemon", True)
        super().__init__(*args, **kwargs, daemon=daemon)
        # Since we have already run 'oc project', our kube config has auth info
        config.load_kube_config()
        self._watcher = None
        self.namespace = namespace
        self.v1_client = client.CoreV1Api()

    def get_all_events(self, _events=None, _continue=None):
        """
        Retrieve all events from the given namespace

        Uses _continue functionality recursively
        """
        if not _events:
            _events = []

        if _continue:
            v1_event_list = self.v1_client.list_namespaced_event(
                self.namespace, _continue=_continue
            )
        else:
            v1_event_list = self.v1_client.list_namespaced_event(self.namespace)

        _events.extend(v1_event_list.items)
        if v1_event_list.metadata._continue:
            self.get_all_events(_events, v1_event_list.metadata._continue)

        return _events

    def run(self):
        """
        Log all events generated for a namespace

        Will only log events that were generated after the EventWatcher thread has started
        """
        log.info("Starting event watcher on namespace '%s'", self.namespace)

        # Get the events from the API and grab the last one listed (if there is one)
        # We only want to show events that appear AFTER this event.
        # This is similar in function to 'oc get events --watch-only=true'
        old_event_time = pytz.utc.localize(datetime.datetime.min)
        old_events = self.get_all_events()
        if old_events:
            last_old_event = sorted(old_events, key=lambda x: x.last_timestamp)[-1]
            old_event_time = last_old_event.last_timestamp

        self._watcher = watch.Watch()
        last_event_info = None
        for event in self._watcher.stream(self.v1_client.list_namespaced_event, self.namespace):
            obj = event["object"]
            event_info = (
                obj.last_timestamp,
                obj.involved_object.kind,
                obj.involved_object.name,
                obj.type,
                obj.reason,
                obj.message,
            )
            # Only print new events, and don't print repeat events
            if obj.last_timestamp > old_event_time and event_info != last_event_info:
                log.info(" --> [%s] [%s %s] [%s - %s] %s", *event_info)
                last_event_info = event_info

    def stop(self):
        log.info("Stopping event watcher on namespace '%s'", self.namespace)
        self._watcher.stop()


def start_event_watcher(namespace):
    try:
        event_watcher = EventWatcher(namespace)
    except Exception:
        log.exception("Failed to init event watcher, unable to monitor events")
        event_watcher = None

    if event_watcher:
        event_watcher.start()

    return event_watcher
