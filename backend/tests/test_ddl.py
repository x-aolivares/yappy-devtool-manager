from yappy_library.application.database.sync import ddl


def test_drop_table_script():
    assert ddl.drop_table_script("yappy", "orders") == "DROP TABLE `yappy`.`orders`;"


def test_drop_table_script_escapes_backticks():
    assert ddl.drop_table_script("ya`ppy", "or`ders") == "DROP TABLE `ya``ppy`.`or``ders`;"


def test_drop_procedure_script():
    assert ddl.drop_procedure_script("yappy", "proc") == "DROP PROCEDURE `yappy`.`proc`;"


def test_replace_table_script_drops_if_exists_then_creates():
    """Un solo script para los dos casos: el `IF EXISTS` no tiene nada que borrar
    cuando la tabla no está, y se la lleva cuando sí."""
    assert ddl.replace_table_script("CREATE TABLE `t` (`id` INT)", "s", "t") == (
        "DROP TABLE IF EXISTS `s`.`t`;\nCREATE TABLE `t` (`id` INT)"
    )


def test_replace_table_script_keeps_the_source_ddl_verbatim():
    """Multi-línea tal cual la devuelve SHOW CREATE: el destino tiene que quedar
    con la definición del origen, no con una reescritura."""
    ddl_b = (
        "CREATE TABLE `t` (\n"
        "  `id` int NOT NULL,\n"
        "  `name` varchar(64) DEFAULT NULL COMMENT 'n',\n"
        "  PRIMARY KEY (`id`)\n"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    )
    script = ddl.replace_table_script(ddl_b, "s", "t")
    assert script == f"DROP TABLE IF EXISTS `s`.`t`;\n{ddl_b}"
    assert script.count("CREATE TABLE") == 1


def test_drop_table_if_exists_script_is_the_same_wording_the_replace_uses():
    """Una sincronización de schema entero suelta todos los DROP y después todos
    los CREATE, así que la línea se pide sola. El texto tiene que ser idéntico al
    de `replace_table_script`: si divergen, el mismo script deja de funcionar
    según cómo se generó."""
    assert ddl.drop_table_if_exists_script("s", "t") == "DROP TABLE IF EXISTS `s`.`t`;"
    assert ddl.drop_table_if_exists_script("ya`ppy", "or`ders") == (
        "DROP TABLE IF EXISTS `ya``ppy`.`or``ders`;"
    )
    replace = ddl.replace_table_script("CREATE TABLE `t` (`id` INT)", "s", "t")
    assert replace == f"{ddl.drop_table_if_exists_script('s', 't')}\nCREATE TABLE `t` (`id` INT)"


def test_replace_table_script_escapes_backticks():
    assert ddl.replace_table_script("CREATE TABLE `t` (`id` INT)", "ya`ppy", "or`ders") == (
        "DROP TABLE IF EXISTS `ya``ppy`.`or``ders`;\nCREATE TABLE `t` (`id` INT)"
    )


def test_replace_table_script_does_not_double_the_trailing_semicolon():
    assert not ddl.replace_table_script("CREATE TABLE `t` (`id` INT);", "s", "t").endswith(";;")


def test_drop_function_script():
    assert ddl.drop_function_script("yappy", "calc") == "DROP FUNCTION `yappy`.`calc`;"


def test_drop_function_script_escapes_backticks():
    assert ddl.drop_function_script("ya`ppy", "ca`lc") == (
        "DROP FUNCTION `ya``ppy`.`ca``lc`;"
    )


def test_create_function_script_strips_the_definer_and_keeps_the_body():
    create = (
        "CREATE DEFINER=`root`@`localhost` FUNCTION `calc`(x int) RETURNS int\n"
        "DETERMINISTIC\n"
        "BEGIN\n"
        "  RETURN x + 1;\n"
        "END"
    )
    script = ddl.create_function_script(create)
    assert script.startswith("CREATE FUNCTION `calc`(x int) RETURNS int")
    assert "DEFINER" not in script
    assert "RETURN x + 1;" in script
    assert "DETERMINISTIC\nBEGIN" in script


def test_replace_function_script_drops_if_exists_then_creates():
    create = "CREATE DEFINER=`u`@`h` FUNCTION `calc`() RETURNS int RETURN 1"
    assert ddl.replace_function_script(create, "yappy", "calc") == (
        "DROP FUNCTION IF EXISTS `yappy`.`calc`;\n"
        "CREATE FUNCTION `calc`() RETURNS int RETURN 1"
    )
