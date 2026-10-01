from yappy_library.application.database.sync import ddl


def test_drop_table_script():
    assert ddl.drop_table_script("yappy", "orders") == "DROP TABLE `yappy`.`orders`;"


def test_drop_table_script_escapes_backticks():
    assert ddl.drop_table_script("ya`ppy", "or`ders") == "DROP TABLE `ya``ppy`.`or``ders`;"


def test_drop_procedure_script():
    assert ddl.drop_procedure_script("yappy", "proc") == "DROP PROCEDURE `yappy`.`proc`;"
