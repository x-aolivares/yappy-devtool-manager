def test_init_powershell_uses_command_type_application(capsys):
    from yappy_cli.cli import init

    init("powershell")

    out = capsys.readouterr().out
    assert "-CommandType Application" in out
    assert "$script:YappyExe" in out
    assert "(yappy workspace)" not in out
    assert "yappy workspace" not in out
    assert "yappy home" not in out
    assert "yappy @args" not in out
