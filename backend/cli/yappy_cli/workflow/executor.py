from yappy_library.adapters.kafka.service import DevUtils, KafkaUnavailable
from yappy_library.adapters.logging import die
from yappy_library.application.session import Session


def executor(environment: str = "dev") -> tuple:
    session = Session(environment).start()

    db = session.database()
    print(f"DB tunnel ready on localhost:{db.port}")

    cap = session.multiple.pf(ports=[8402, 8403], load_balance="cap")
    cap2 = session.multiple.pf(ports=[8412, 8413], load_balance="cap2")
    bastion = session.bastion.pf(ports=[9091])

    # `KafkaUnavailable` is raised instead of exiting so the web can refuse a
    # start instead of dying. This file is a terminal script, so the exit is
    # re-made here — same message, same code as before.
    kafka = DevUtils().kafka()
    for target in ("server", "ui"):
        try:
            kafka.up(target)
        except KafkaUnavailable as exc:
            die(exc.reason)

    return session, kafka


if __name__ == "__main__":
    import atexit

    session, kafka = executor()
    atexit.register(lambda: [session.cleanup(), kafka.cleanup()])
