from yappy_cli.api import Session, DevUtils


def executor(environment: str = "dev") -> tuple:
    session = Session(environment).start()

    db = session.database()
    print(f"DB tunnel ready on localhost:{db.port}")

    cap = session.multiple.pf(ports=[8402, 8403], load_balance="cap")
    cap2 = session.multiple.pf(ports=[8412, 8413], load_balance="cap2")
    bastion = session.bastion.pf(ports=[9091])

    kafka = DevUtils().kafka()
    kafka.up("server")
    kafka.up("ui")

    return session, kafka


if __name__ == "__main__":
    import atexit

    session, kafka = executor()
    atexit.register(lambda: [session.cleanup(), kafka.cleanup()])
