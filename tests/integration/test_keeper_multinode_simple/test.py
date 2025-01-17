import time

import pytest

import helpers.keeper_utils as keeper_utils
from helpers.cluster import ClickHouseCluster
from helpers.network import PartitionManager
from helpers.test_tools import assert_eq_with_retry

cluster = ClickHouseCluster(__file__)
node1 = cluster.add_instance(
    "node1",
    main_configs=["configs/enable_keeper1.xml", "configs/use_keeper.xml"],
    stay_alive=True,
)
node2 = cluster.add_instance(
    "node2",
    main_configs=["configs/enable_keeper2.xml", "configs/use_keeper.xml"],
    stay_alive=True,
)
node3 = cluster.add_instance(
    "node3",
    main_configs=["configs/enable_keeper3.xml", "configs/use_keeper.xml"],
    stay_alive=True,
)


@pytest.fixture(scope="module")
def started_cluster():
    try:
        cluster.start()

        yield cluster

    finally:
        cluster.shutdown()


def smaller_exception(ex):
    return "\n".join(str(ex).split("\n")[0:2])


def wait_nodes():
    keeper_utils.wait_nodes(cluster, [node1, node2, node3])


def get_fake_zk(nodename, timeout=30.0):
    return keeper_utils.get_fake_zk(cluster, nodename, timeout=timeout)


def test_read_write_multinode(started_cluster):
    try:
        wait_nodes()
        node1_zk = get_fake_zk("node1")
        node2_zk = get_fake_zk("node2")
        node3_zk = get_fake_zk("node3")

        # Cleanup
        if node1_zk.exists("/test_read_write_multinode_node1") != None:
            node1_zk.delete("/test_read_write_multinode_node1")

        node1_zk.create("/test_read_write_multinode_node1", b"somedata1")
        node2_zk.create("/test_read_write_multinode_node2", b"somedata2")
        node3_zk.create("/test_read_write_multinode_node3", b"somedata3")

        # stale reads are allowed
        while node1_zk.exists("/test_read_write_multinode_node2") is None:
            time.sleep(0.1)

        while node1_zk.exists("/test_read_write_multinode_node3") is None:
            time.sleep(0.1)

        while node2_zk.exists("/test_read_write_multinode_node3") is None:
            time.sleep(0.1)

        assert node3_zk.get("/test_read_write_multinode_node1")[0] == b"somedata1"
        assert node2_zk.get("/test_read_write_multinode_node1")[0] == b"somedata1"
        assert node1_zk.get("/test_read_write_multinode_node1")[0] == b"somedata1"

        assert node3_zk.get("/test_read_write_multinode_node2")[0] == b"somedata2"
        assert node2_zk.get("/test_read_write_multinode_node2")[0] == b"somedata2"
        assert node1_zk.get("/test_read_write_multinode_node2")[0] == b"somedata2"

        assert node3_zk.get("/test_read_write_multinode_node3")[0] == b"somedata3"
        assert node2_zk.get("/test_read_write_multinode_node3")[0] == b"somedata3"
        assert node1_zk.get("/test_read_write_multinode_node3")[0] == b"somedata3"

    finally:
        try:
            for zk_conn in [node1_zk, node2_zk, node3_zk]:
                zk_conn.stop()
                zk_conn.close()
        except:
            pass


def test_watch_on_follower(started_cluster):
    try:
        wait_nodes()
        node1_zk = get_fake_zk("node1")
        node2_zk = get_fake_zk("node2")
        node3_zk = get_fake_zk("node3")

        # Cleanup
        if node1_zk.exists("/test_data_watches") != None:
            node1_zk.delete("/test_data_watches")

        node1_zk.create("/test_data_watches")
        node2_zk.set("/test_data_watches", b"hello")
        node3_zk.set("/test_data_watches", b"world")

        node1_data = None

        def node1_callback(event):
            print("node1 data watch called")
            nonlocal node1_data
            node1_data = event

        node1_zk.get("/test_data_watches", watch=node1_callback)

        node2_data = None

        def node2_callback(event):
            print("node2 data watch called")
            nonlocal node2_data
            node2_data = event

        node2_zk.get("/test_data_watches", watch=node2_callback)

        node3_data = None

        def node3_callback(event):
            print("node3 data watch called")
            nonlocal node3_data
            node3_data = event

        node3_zk.get("/test_data_watches", watch=node3_callback)

        node1_zk.set("/test_data_watches", b"somevalue")
        time.sleep(3)

        print(node1_data)
        print(node2_data)
        print(node3_data)

        assert node1_data == node2_data
        assert node3_data == node2_data

    finally:
        try:
            for zk_conn in [node1_zk, node2_zk, node3_zk]:
                zk_conn.stop()
                zk_conn.close()
        except:
            pass


def test_session_expiration(started_cluster):
    try:
        wait_nodes()
        node1_zk = get_fake_zk("node1")
        node2_zk = get_fake_zk("node2")
        node3_zk = get_fake_zk("node3", timeout=3.0)
        print("Node3 session id", node3_zk._session_id)

        # Cleanup
        if node3_zk.exists("/test_ephemeral_node") != None:
            node3_zk.delete("/test_ephemeral_node")

        node3_zk.create("/test_ephemeral_node", b"world", ephemeral=True)

        with PartitionManager() as pm:
            pm.partition_instances(node3, node2)
            pm.partition_instances(node3, node1)
            node3_zk.stop()
            node3_zk.close()
            for _ in range(100):
                if (
                    node1_zk.exists("/test_ephemeral_node") is None
                    and node2_zk.exists("/test_ephemeral_node") is None
                ):
                    break
                print("Node1 exists", node1_zk.exists("/test_ephemeral_node"))
                print("Node2 exists", node2_zk.exists("/test_ephemeral_node"))
                time.sleep(0.1)
                node1_zk.sync("/")
                node2_zk.sync("/")

        assert node1_zk.exists("/test_ephemeral_node") is None
        assert node2_zk.exists("/test_ephemeral_node") is None

    finally:
        try:
            for zk_conn in [node1_zk, node2_zk, node3_zk]:
                try:
                    zk_conn.stop()
                    zk_conn.close()
                except:
                    pass
        except:
            pass


def test_follower_restart(started_cluster):
    try:
        wait_nodes()
        node1_zk = get_fake_zk("node1")
        node3_zk = get_fake_zk("node3")

        # Cleanup
        if node1_zk.exists("/test_restart_node") != None:
            node1_zk.delete("/test_restart_node")

        node1_zk.create("/test_restart_node", b"hello")
        node3.restart_clickhouse(kill=True)

        wait_nodes()

        node3_zk = get_fake_zk("node3")
        # got data from log
        assert node3_zk.get("/test_restart_node")[0] == b"hello"

    finally:
        try:
            for zk_conn in [node1_zk, node3_zk]:
                try:
                    zk_conn.stop()
                    zk_conn.close()
                except:
                    pass
        except:
            pass


def test_simple_replicated_table(started_cluster):
    wait_nodes()

    for i, node in enumerate([node1, node2, node3]):
        node.query("DROP TABLE IF EXISTS t SYNC")
        node.query(
            f"CREATE TABLE t (value UInt64) ENGINE = ReplicatedMergeTree('/clickhouse/t', '{i + 1}') ORDER BY tuple()"
        )

    node2.query("INSERT INTO t SELECT number FROM numbers(10)")

    node1.query("SYSTEM SYNC REPLICA t", timeout=10)
    node3.query("SYSTEM SYNC REPLICA t", timeout=10)

    assert_eq_with_retry(node1, "SELECT COUNT() FROM t", "10")
    assert_eq_with_retry(node2, "SELECT COUNT() FROM t", "10")
    assert_eq_with_retry(node3, "SELECT COUNT() FROM t", "10")
