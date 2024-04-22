from functools import partial
from io import StringIO
from json.encoder import py_encode_basestring_ascii
from unittest import mock

import pytest
from twisted.logger import Logger as TwistedLogger
from twisted.logger import (
    LogLevel,
    formatEvent,
    globalLogPublisher,
    jsonFileLogObserver,
)

from nucypher.utilities.emitters import StdoutEmitter
from nucypher.utilities.logging import GlobalLoggerSettings, Logger


def naive_print_observer(event):
    print(formatEvent(event), end="")


def get_json_observer_for_file(logfile):
    def json_observer(event):
        observer = jsonFileLogObserver(outFile=logfile)
        return observer(event)
    return json_observer


def expected_processing(string_with_curly_braces):
    ascii_string = py_encode_basestring_ascii(string_with_curly_braces)[1:-1]
    expected_output = Logger.escape_format_string(ascii_string)
    return expected_output


# Any string without curly braces won't have any problem
ordinary_strings = (
    "Because there's nothing worse in life than being ordinary.",
    "🍌 🍌 🍌 terracotta 🍌 🍌 🍌 terracotta terracotta 🥧",
    '"You can quote me on this"',
    f"Some bytes: {b''.join(chr(i).encode() for i in range(1024) if chr(i) not in '{}')}"
)

# Strings that have curly braces but that appear in groups of even length are considered safe too,
# as curly braces are escaped this way, according to PEP 3101. Twisted will eat these just fine,
# but since they have curly braces, we will have to process them in our Logger.
quirky_strings = (
    "{{}}", "{{hola}}", "{{{{}}}}", "foo{{}}",
)

# These are strings that are definitely going to cause trouble for Twisted Logger
freaky_format_strings = (  # Including the expected exception and error message
    ("{", ValueError, "Single '{' encountered in format string"),
    ("}", ValueError, "Single '}' encountered in format string"),
    ("foo}", ValueError, "Single '}' encountered in format string"),
    ("bar{", ValueError, "Single '{' encountered in format string"),
    ("}{", ValueError, "Single '}' encountered in format string"),
    ("{{}", ValueError, "Single '}' encountered in format string"),
    ("}}{", ValueError, "Single '{' encountered in format string"),
    (f"{b'{'}", ValueError, "expected '}' before end of string"),
    (f"{b'}'}", ValueError, "Single '}' encountered in format string"),
    ("{}", KeyError, ""),
    ("{}{", KeyError, ""),
    ("{}}", KeyError, ""),
    ("{{{}}}", KeyError, ""),
    ("{{{{{}}}}}", KeyError, ""),
    ("{bananas}", KeyError, "bananas"),
    (str({'bananas': '🍌🍌🍌'}), KeyError, "bananas"),
    (f"Some bytes: {b''.join(chr(i).encode() for i in range(1024))}", KeyError, "|")
)

# Embrace the quirky!
acceptable_strings = (*ordinary_strings, *quirky_strings)


def test_twisted_logger_doesnt_like_curly_braces(capsys):
    twisted_logger = TwistedLogger('twisted', observer=naive_print_observer)

    # Normal strings are logged normally
    for string in acceptable_strings:
        twisted_logger.info(string)
        captured = capsys.readouterr()
        assert string.format() == captured.out
        assert not captured.err

    # But curly braces are not
    for string, _exception, exception_message in freaky_format_strings:
        twisted_logger.info(string)
        captured = capsys.readouterr()
        assert string != captured.out
        assert "Unable to format event" in captured.out
        assert exception_message in captured.out


def test_twisted_json_logger_doesnt_like_curly_braces_either():
    twisted_logger = TwistedLogger('twisted-json')

    # Normal strings are logged normally
    for string in acceptable_strings:
        file = StringIO()
        twisted_logger.observer = get_json_observer_for_file(file)
        twisted_logger.info(string)
        logged_event = file.getvalue()
        assert '"log_level": {"name": "info"' in logged_event
        assert f'"log_format": "{expected_processing(string.format())}"' in logged_event

    # But curly braces are not
    for string, exception, exception_message in freaky_format_strings:
        file = StringIO()
        twisted_logger.observer = get_json_observer_for_file(file)
        with pytest.raises(exception, match=exception_message):
            twisted_logger.info(string)


def test_but_nucypher_logger_is_cool_with_that(capsys):
    nucypher_logger = Logger('nucypher-logger', observer=naive_print_observer)

    # Normal strings are logged normally
    for string in acceptable_strings:
        nucypher_logger.info(string)
        captured = capsys.readouterr()
        assert string == captured.out
        assert not captured.err

    # And curly braces too!
    for string, _exception, _exception_message in freaky_format_strings:
        nucypher_logger.info(string)
        captured = capsys.readouterr()
        assert "Unable to format event" not in captured.out
        assert not captured.err
        assert string == captured.out


def test_even_nucypher_json_logger_is_cool():

    nucypher_logger = Logger('nucypher-logger-json')

    # Normal strings are logged normally
    for string in acceptable_strings:
        file = StringIO()
        nucypher_logger.observer = get_json_observer_for_file(file)
        nucypher_logger.info(string)
        logged_event = file.getvalue()
        assert '"log_level": {"name": "info"' in logged_event
        assert f'"log_format": "{expected_processing(string)}"' in logged_event

    # And curly braces too!
    for string, _exception, _exception_message in freaky_format_strings:
        file = StringIO()
        nucypher_logger.observer = get_json_observer_for_file(file)
        nucypher_logger.info(string)
        logged_event = file.getvalue()
        assert '"log_level": {"name": "info"' in logged_event
        assert f'"log_format": "{expected_processing(string)}"' in logged_event


def test_pause_all_logging_while():
    # get state beforehand
    former_global_observers = dict(GlobalLoggerSettings._observers)
    former_registered_observers = list(globalLogPublisher._observers)
    with GlobalLoggerSettings.pause_all_logging_while():
        # within context manager
        assert len(GlobalLoggerSettings._observers) == 0
        assert len(globalLogPublisher._observers) == 0

    # exited context manager
    assert former_global_observers == GlobalLoggerSettings._observers
    assert former_registered_observers == globalLogPublisher._observers


@pytest.mark.parametrize("global_log_level", LogLevel._enumerants.values())
def test_log_level_adhered_to(global_log_level):
    old_log_level = GlobalLoggerSettings.log_level.name
    try:
        GlobalLoggerSettings.set_log_level(global_log_level.name)

        received_events = []

        def logger_observer(event):
            received_events.append(event)

        logger = Logger("test-logger")
        logger.observer = logger_observer

        message = "People without self-doubt should never put themselves in a position of complete power"  # - Chuck Rhoades (Billions)
        num_logged_events = 0

        for level in LogLevel._enumerants.values():
            # call logger.<level>(message)
            getattr(logger, level.name)(message)

            if level >= global_log_level:
                num_logged_events += 1
            # else not logged

            assert len(received_events) == num_logged_events
    finally:
        GlobalLoggerSettings.set_log_level(old_log_level)


@pytest.mark.parametrize(
    "start_logging_fn, stop_logging_fn, logging_type",
    (
        (
            GlobalLoggerSettings.start_console_logging,
            GlobalLoggerSettings.stop_console_logging,
            GlobalLoggerSettings.LoggingType.CONSOLE,
        ),
        (
            GlobalLoggerSettings.start_text_file_logging,
            GlobalLoggerSettings.stop_text_file_logging,
            GlobalLoggerSettings.LoggingType.TEXT,
        ),
        (
            GlobalLoggerSettings.start_json_file_logging,
            GlobalLoggerSettings.stop_json_file_logging,
            GlobalLoggerSettings.LoggingType.JSON,
        ),
        (
            partial(GlobalLoggerSettings.start_sentry_logging, dsn="mock_dsn"),
            GlobalLoggerSettings.stop_sentry_logging,
            GlobalLoggerSettings.LoggingType.SENTRY,
        ),
    ),
)
@mock.patch("nucypher.utilities.logging.initialize_sentry", return_value=None)
def test_addition_removal_global_observers(
    sentry_mock_init, start_logging_fn, stop_logging_fn, logging_type
):
    with GlobalLoggerSettings.pause_all_logging_while():
        # start logging
        start_logging_fn()
        assert len(globalLogPublisher._observers) == 1
        assert len(GlobalLoggerSettings._observers) == 1
        original_global_observer = GlobalLoggerSettings._observers[logging_type]
        assert original_global_observer is not None
        assert original_global_observer in globalLogPublisher._observers

        # try starting again - noop
        start_logging_fn()
        assert len(globalLogPublisher._observers) == 1
        assert original_global_observer in globalLogPublisher._observers
        assert len(GlobalLoggerSettings._observers) == 1
        assert GlobalLoggerSettings._observers[logging_type] == original_global_observer

        # stop logging
        stop_logging_fn()
        assert len(globalLogPublisher._observers) == 0
        assert len(GlobalLoggerSettings._observers) == 0

        # try stopping again, when already stopped/removed - noop
        stop_logging_fn()


def test_stdout_emitter_link_to_logging(mocker):
    message = "Learn from the mistakes of others. You can’t live long enough to make them all yourself."  # - Eleanor Roosevelt
    emit_fn = mocker.Mock()
    with mocker.patch("nucypher.utilities.logging.Logger.emit", side_effect=emit_fn):
        call_count = 0
        assert emit_fn.call_count == call_count

        emitter = StdoutEmitter()

        # message(...)
        for verbosity in [0, 1, 2]:
            emitter.message(message=message, verbosity=verbosity)
            call_count += 1
            assert emit_fn.call_count == call_count

            if verbosity < 2:
                emit_fn.assert_called_with(LogLevel.info, message)
            else:
                emit_fn.assert_called_with(LogLevel.debug, message)

        # echo(...)
        for verbosity in [0, 1, 2]:
            emitter.echo(message=message, verbosity=verbosity)
            assert (
                emit_fn.call_count == call_count
            ), "user interactions so no change in call count"

        # error(...)
        exception = ValueError(message)
        emitter.error(exception)
        call_count += 1
        assert emit_fn.call_count == call_count
        emit_fn.assert_called_with(LogLevel.error, str(exception))

        # banner(...)
        emitter.banner(banner=message)
        assert (
            emit_fn.call_count == call_count
        ), "just a banner so no change in call count"
