import json
import logging
from typing import cast
from uuid import uuid4

import pytest
from pydantic import BaseModel

from heizer import ConsumerSignal, Message, Producer, ProducerConfig, Topic, consumer, create_new_topic


@pytest.fixture
def group_id():
    return "test_group"


@pytest.fixture
def producer_config(bootstrap_server):
    return ProducerConfig(bootstrap_servers=bootstrap_server)


@pytest.fixture
def consumer_config(group_id, bootstrap_server):
    return {
        "bootstrap.servers": bootstrap_server,
        "group.id": group_id,
        "auto.offset.reset": "earliest",
    }


@pytest.mark.parametrize("group_id", ["test_consumer_stopper"])
def test_consumer_stopper(group_id, consumer_config, producer_config, caplog, bootstrap_server) -> None:
    topics = [Topic(name=f"heizer.test.result.{uuid4()}", num_partitions=3)]
    create_new_topic({"bootstrap.servers": bootstrap_server}, topics)

    pd = Producer(config=producer_config)

    for status, result in [("start", 1), ("loading", 2), ("success", 3), ("postprocess", 4)]:
        pd.produce(
            topic=topics[0],
            key="key1",
            value={"status": status, "result": result},
            headers={"header1": "value1", "header2": "value2"},
            auto_flush=False,
        )

    pd.flush()

    def stopper(msg: Message) -> bool:
        data = json.loads(msg.value)
        if data["status"] == "success":
            return True
        return False

    @consumer(
        topics=topics,
        config=consumer_config,
        stopper=stopper,
    )
    def consume_data(msg, *args, **kwargs) -> str:
        data = json.loads(msg.value)

        assert msg.key == "key1"
        assert msg.headers == {"header1": "value1", "header2": "value2"}

        return cast(str, data["result"])

    result = consume_data()  # type: ignore

    assert result == 3


@pytest.mark.parametrize("group_id", ["test_consumer_call_once"])
def test_consumer_call_once(group_id, producer_config, consumer_config, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    topic_name = "heizer.test.test_consumer_call_once"
    topic = Topic(name=f"{topic_name}.{uuid4()}")

    producer = Producer(config=producer_config)

    for status, result in [("start", 1), ("loading", 2), ("success", 3), ("postprocess", 4)]:
        producer.produce(
            topic=topic,
            key="key1",
            value={"status": status, "result": result},
            headers={"header1": "value1", "header2": "value2"},
            auto_flush=True,
        )

    @consumer(topics=[topic], config=consumer_config, call_once=True)
    def consume_data(msg, *args, **kwargs) -> str:
        data = json.loads(msg.value)
        return data["result"]

    result = consume_data()

    assert result == 1


@pytest.mark.parametrize("group_id", ["test_stop_consumer_by_signal"])
def test_stop_consumer_by_signal(group_id, producer_config, consumer_config, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    topic_name = "heizer.test.test_stop_consumer_by_signal"
    topic = Topic(name=f"{topic_name}.{uuid4()}")

    producer = Producer(config=producer_config)

    for status, result in [("start", 1), ("loading", 2)]:
        producer.produce(
            topic=topic,
            key="key1",
            value={"status": status, "result": result},
            headers={"header1": "value1", "header2": "value2"},
            auto_flush=True,
        )
    sg = ConsumerSignal()

    @consumer(topics=[topic], config=consumer_config, consumer_signal=sg)
    def consume_data(msg, *args, **kwargs) -> str:
        data = json.loads(msg.value)
        sg.stop()
        return data["result"]

    result = consume_data()

    assert result == 1


@pytest.mark.parametrize("group_id", ["test_consumer_deserializer"])
def test_consumer_deserializer(caplog, consumer_config, group_id, producer_config) -> None:
    caplog.set_level(logging.DEBUG)
    topic = Topic(f"heizer.test.test_consumer_deserializer.{uuid4()}")

    class TestModel(BaseModel):
        name: str
        age: int

    deserializer = TestModel.parse_raw

    producer = Producer(config=producer_config)

    producer.produce(
        topic=topic,
        value={
            "name": "mike",
            "age": 20,
        },
    )

    @consumer(
        topics=[topic],
        config=consumer_config,
        call_once=True,
        deserializer=deserializer,
    )
    def consume_data(message: Message, C, *args, **kwargs):
        C.consumer_signal.stop()
        return message.formatted_value

    result = consume_data()

    assert isinstance(result, TestModel)

    assert result.name == "mike"
    assert result.age == 20
