"""Tests for the ``requires_lock`` decorator in ``utilities.py``."""

import threading
from time import sleep, time

import pytest

from openflexure_microscope_server.utilities import requires_lock


class LockableClass:
    """A class with methods that can be locked with the ``requires_lock`` decorator."""

    def __init__(self):
        """Initialise the LockableClass with a rentrant lock."""
        self._lock = threading.RLock()

    @property
    @requires_lock
    def test_property(self) -> int:
        """A property that always returns 1."""
        return 1

    @requires_lock
    def fast_task(self) -> int:
        """Instantly return the number 2."""
        return 2

    @requires_lock
    def slow_task(self) -> int:
        """Take 0.5s to return the number 3."""
        sleep(0.5)
        return 3

    @requires_lock
    def task_that_calls_fast_task(self) -> int:
        """Require a lock and call a fast task that requires it too."""
        return self.fast_task()

    @requires_lock
    def task_that_calls_property(self) -> int:
        """Require a lock and call a property that requires it too."""
        return self.test_property


@pytest.fixture
def lockable_obj() -> LockableClass:
    """Return an instance of LockableClass."""
    return LockableClass()


def test_error_if_no_lock(lockable_obj):
    """Test there is an AttributeError if the object has no _lock attribute."""
    # Delete the lock!
    del lockable_obj._lock
    with pytest.raises(AttributeError) as exec_info:
        lockable_obj.test_property
    assert str(exec_info.value) == "LockableClass has no '_lock' attribute"


def test_error_if_lock_of_wrong_type(lockable_obj):
    """Test there is an TypeError if the object's _lock attribute isn't a lock."""
    # Delete the lock!
    lockable_obj._lock = "I'm a mock, not a lock!"
    with pytest.raises(TypeError) as exec_info:
        lockable_obj.test_property
    expected_msg = "requires_lock requires self._lock to be a lock"
    assert str(exec_info.value) == expected_msg


def test_functionality(lockable_obj):
    """Check that this doesn't affect the most basic functionality of the class."""
    assert lockable_obj.test_property == 1
    assert lockable_obj.fast_task() == 2
    # Don't check slow task here as it is slow and functionally no different from fast
    # task.
    # Check tasks that call other tasks locked to check that they are rentrant
    assert lockable_obj.task_that_calls_property() == 1
    assert lockable_obj.task_that_calls_fast_task() == 2


def check_fast_target_waits(lockable_obj, fast_target):
    """Start threads with a slow target and the supplied fast target, check lock works.

    :param lockable_obj: An instance of LockableClass
    :param fast_target: The callable to test with the fast thread.

    The program runs by:

    * First checking that fast_target when run on its own completes in less than
        0.1s
    * Starts the thread with the slow_target.
    * Starts the thread with the fast_target straight afterwards.
    * Wait for the fast target to complete.
    * Check the slow target has completeted once returned (showing that the fast
        target had to wait because of the lock)
    * Check that the time elapsed is over 0.4s.
    """
    # On its own the fast thread is fast.
    fast_thread = threading.Thread(target=fast_target)
    t_start = time()
    fast_thread.start()
    fast_thread.join()
    assert time() - t_start < 0.1

    # If slow thread is running it has to wait.
    fast_thread = threading.Thread(target=fast_target)
    slow_thread = threading.Thread(target=lockable_obj.slow_task)

    t_start = time()
    slow_thread.start()
    fast_thread.start()
    fast_thread.join()
    # slow task must have finished
    assert not slow_thread.is_alive()
    assert time() - t_start > 0.4


def test_locking(lockable_obj):
    """Test the locking works when calling decorated methods in two threads."""
    check_fast_target_waits(lockable_obj, lockable_obj.fast_task)


def test_property_locking(lockable_obj):
    """Test that the locking works when calling a property not a function.

    The target is a method with no lock, that calls the property that requires
    the lock.
    """

    def property_caller() -> int:
        return lockable_obj.test_property

    check_fast_target_waits(lockable_obj, property_caller)
