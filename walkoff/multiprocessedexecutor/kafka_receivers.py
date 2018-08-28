import logging

import gevent
from confluent_kafka import Consumer, KafkaError
from flask import Flask

import walkoff.config
from walkoff.events import WalkoffEvent
from walkoff.multiprocessedexecutor.protoconverter import ProtobufWorkflowResultsConverter
from walkoff.server import context

logger = logging.getLogger(__name__)


class KafkaWorkflowResultsReceiver(object):
    _requires = ['confluent-kafka']

    def __init__(self, current_app=None, message_converter=ProtobufWorkflowResultsConverter):
        import walkoff.server.workflowresults  # Need this import

        kafka_config = walkoff.config.Config.WORKFLOW_RESULTS_KAFKA_CONFIG
        self.receiver = Consumer(kafka_config)
        self.topic = kafka_config['topic']
        self.message_converter = message_converter
        self.exit = False
        self.workflows_executed = 0

        if current_app is None:
            self.current_app = Flask(__name__)
            self.current_app.config.from_object(walkoff.config.Config)
            self.current_app.running_context = context.Context(walkoff.config.Config, init_all=False)
        else:
            self.current_app = current_app

    def shutdown(self):
        self.exit = True
        self.receiver.close()

    def receive_results(self):
        """Constantly receives data from the Kafka Consumer and handles it accordingly"""
        logger.info('Starting Kafka workflow results receiver')
        while not self.exit:
            raw_message = self.receiver.poll(1.0)
            if raw_message is None:
                gevent.sleep(0.1)
                continue
            if raw_message.error():
                if raw_message.error().code() == KafkaError._PARTITION_EOF:
                    gevent.sleep(0.1)
                    continue
                else:
                    logger.error('Received an error in Kafka receiver: {}'.format(raw_message.error()))
                    gevent.sleep(0.1)
                    continue
            with self.current_app.app_context():
                self._send_callback(raw_message.value())
        return

    def _send_callback(self, message_bytes):
        event, sender, data = self.message_converter.to_event_callback(message_bytes)

        if sender is not None and event is not None:
            with self.current_app.app_context():
                event.send(sender, data=data)
            if event in [WalkoffEvent.WorkflowShutdown, WalkoffEvent.WorkflowAborted]:
                self._increment_execution_count()

    def _increment_execution_count(self):
        self.workflows_executed += 1


def make_kafka_results_receiver(**kwargs):
    return KafkaWorkflowResultsReceiver(**kwargs)
