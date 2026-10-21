"""Ni escrituras a medias, ni ediciones que se pisan.

Tres cosas que se arreglan acá:

1. **Un archivo a medias.** `path.write_text(...)` trunca y reescribe: entre
   las dos operaciones el archivo está incompleto. Para el tracker eso era
   visible —el parser del JSON truncado devuelve ``None`` y la fila
   **desaparece** del snapshot, así que la UI parpadea—. Para
   `~/.aws/credentials` era peor: un corte a mitad dejaba al developer sin
   claves de AWS.

2. **Dos escritores a la vez.** El tracker lo escriben el CLI y la web, que son
   procesos distintos. El catálogo de puertos lo edita la pantalla "en cada
   tecla". Un read-modify-write sin lock pierde una de las dos ediciones.

3. **Dos túneles en el mismo puerto.** El chequeo "ya hay un túnel sano" y el
   spawn tienen que ser una sola operación: dos requests concurrentes veían
   los dos "no hay túnel" y spawneaban los dos. El segundo no puede tomar el
   puerto y **falla en silencio**.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from yappy_library.adapters import process_tracker
from yappy_library.adapters.file_io import file_lock, lock_path_for, write_atomic


# --- escribir sin dejar nada a medias --------------------------------------


def test_la_escritura_atómica_no_deja_archivos_temporales(tmp_path):
    destino = tmp_path / "datos.json"

    write_atomic(destino, '{"a": 1}')

    assert destino.read_text(encoding="utf-8") == '{"a": 1}'
    assert list(tmp_path.iterdir()) == [destino], "quedó un temporal"


def test_la_escritura_atómica_reemplaza_completo(tmp_path):
    """El archivo nuevo entero, no el viejo con el nuevo encima."""
    destino = tmp_path / "datos.json"
    destino.write_text("contenido viejo que es más largo", encoding="utf-8")

    write_atomic(destino, "corto")

    assert destino.read_text(encoding="utf-8") == "corto"


def test_un_lector_nunca_ve_un_archivo_a_medias(tmp_path):
    """Un lector concurrente ve el viejo entero o el nuevo entero.

    Es la property que importa: el parser del tracker devuelve ``None`` ante
    un JSON truncado, y la fila desaparece del snapshot.
    """
    destino = tmp_path / "datos.json"
    largo = "x" * 120_000
    corto = "y" * 40_000
    write_atomic(destino, largo)

    parar = threading.Event()
    leidos: set[int] = set()
    errores: list[Exception] = []

    def _leer():
        while not parar.is_set():
            try:
                leidos.add(len(destino.read_text(encoding="utf-8")))
            except PermissionError:
                # En Windows el rename y un reader concurrente son excluyentes
                # a nivel de syscall: el reader recibe una violación de
                # sharing y tiene que reintentar. Es lo que hace el tracker
                # con su `except OSError`, salvo que ahí el reintento no hace
                # falta porque la ventana es de microsegundos.
                time.sleep(0.001)
            except Exception as exc:  # pragma: no cover - sólo para el assert
                errores.append(exc)

    lector = threading.Thread(target=_leer, daemon=True)
    lector.start()
    try:
        for i in range(80):
            write_atomic(destino, corto if i % 2 else largo)
    finally:
        parar.set()
        lector.join(timeout=5)

    assert not errores, errores
    assert leidos, "el lector no llegó a leer"
    # Los dos tamaños posibles: el viejo y el nuevo. Nunca un medio.
    assert leidos <= {len(largo), len(corto)}, f"tamaños intermedios: {leidos}"


# --- el lock entre procesos ------------------------------------------------


def test_el_lock_serializa_dos_escritores(tmp_path):
    """Uno espera al otro: no se pierde ninguna de las dos ediciones."""
    objetivo = tmp_path / "contador.txt"
    objetivo.write_text("0", encoding="utf-8")

    def _sumar():
        for _ in range(20):
            with file_lock(objetivo):
                valor = int(objetivo.read_text(encoding="utf-8"))
                time.sleep(0.001)
                objetivo.write_text(str(valor + 1), encoding="utf-8")

    hilos = [threading.Thread(target=_sumar) for _ in range(4)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert objetivo.read_text(encoding="utf-8") == "80"


def test_el_lock_se_toma_y_se_suelta(tmp_path):
    objetivo = tmp_path / "datos.txt"
    objetivo.write_text("x", encoding="utf-8")

    with file_lock(objetivo):
        pass

    # Y se puede volver a tomar: un lock que no se suelta cuelga al que viene.
    with file_lock(objetivo):
        pass


def test_el_archivo_del_lock_no_se_confunde_con_el_contenido(tmp_path):
    """El lock vive en otro archivo, y con punto adelante.

    `env.base` se lista con el glob `env.*`: un `env.base.lock` aparecería
    como un ambiente llamado `base.lock`.
    """
    objetivo = tmp_path / "env.base"

    with file_lock(objetivo):
        pass

    lock = lock_path_for(objetivo)
    assert lock.name == ".env.base.lock"
    assert not list(tmp_path.glob("env.*"))


# --- el tracker ------------------------------------------------------------


def test_el_tracker_se_escribe_atómicamente(tmp_path, monkeypatch):
    """Un lector nunca ve una entrada a medias."""
    monkeypatch.setattr(process_tracker, "_TRACKER_DIR", tmp_path / "tracker")

    process_tracker.track_process(4242, "tunnel", target="cap", local_port=8402)

    entradas = process_tracker.get_tracked_processes()
    assert [e["pid"] for e in entradas] == [4242]
    assert entradas[0]["local_port"] == 8402


def test_el_tracker_no_deja_temporales(tmp_path, monkeypatch):
    monkeypatch.setattr(process_tracker, "_TRACKER_DIR", tmp_path / "tracker")

    process_tracker.track_process(4242, "tunnel", target="cap")
    process_tracker.update_process_state(4242, "up")

    assert not [p for p in (tmp_path / "tracker").iterdir() if p.name.endswith(".tmp")]


def test_untrack_no_falla_si_el_archivo_ya_no_está(tmp_path, monkeypatch):
    """El `exists()` + `unlink()` era un TOCTOU: otro proceso podía llevárselo
    entre los dos, y el `FileNotFoundError` salía por `kill_ssm`."""
    monkeypatch.setattr(process_tracker, "_TRACKER_DIR", tmp_path / "tracker")

    process_tracker.untrack_process(999999)  # nunca existió


# --- el token de la base ---------------------------------------------------


def test_el_token_actualiza_solo_su_línea(tmp_path):
    """El archivo se reescribía entero desde un parse ingenuo, y eso perdía
    los comentarios y el orden de las claves."""
    from yappy_library.adapters.database.credentials import write_local_env

    env_local = tmp_path / ".env.local"
    env_local.write_text(
        "# un comentario que importa\nOTRA_CLAVE=valor\nDB_PASSWORD=viejo\n",
        encoding="utf-8",
    )

    write_local_env("token-nuevo", env_local)

    texto = env_local.read_text(encoding="utf-8")
    assert "# un comentario que importa" in texto
    assert "OTRA_CLAVE=valor" in texto
    assert "DB_PASSWORD=token-nuevo" in texto
    assert "viejo" not in texto


def test_el_token_agrega_la_línea_si_no_está(tmp_path):
    from yappy_library.adapters.database.credentials import write_local_env

    env_local = tmp_path / ".env.local"
    env_local.write_text("OTRA=1\n", encoding="utf-8")

    write_local_env("tok", env_local)

    assert "DB_PASSWORD=tok" in env_local.read_text(encoding="utf-8")
    assert "OTRA=1" in env_local.read_text(encoding="utf-8")


# --- el reclamo de un puerto ------------------------------------------------


def test_dos_conexiones_concurrentes_no_abren_dos_túneles(monkeypatch):
    """El check y el spawn tienen que ser una sola operación.

    Dos requests a la vez veían los dos "no hay túnel" y spawneaban los dos.
    El segundo no puede tomar el puerto y falla en silencio.
    """
    from yappy_library.adapters.database import connection

    monkeypatch.setattr(connection, "_local_env_values", lambda: {})
    monkeypatch.setattr(connection, "generate_token", lambda cfg: "tok")

    spawneados = []
    arriba = threading.Event()

    class _Greeting:
        """El primero que pregunta ve "no hay túnel"; los demás, lo que haya.

        El primero **se queda esperando**: sin eso, los otros tres pasan su
        chequeo mientras el primero todavía no spawneó, y la carrera no se
        reproduce. Con el lock, los otros tres ni pueden preguntar hasta que
        el primero termina — y entonces el túnel ya está.
        """

        def __init__(self):
            self.lock = threading.Lock()
            self.consultas = 0
            self.hay_tunel = False

        def __call__(self, port, timeout=1.0):
            with self.lock:
                self.consultas += 1
                numero = self.consultas
            if numero == 1:
                time.sleep(0.2)  # la ventana en la que los demás preguntan
                return False
            with self.lock:
                # `True` significa "hay un túnel sano": reúsalo.
                return self.hay_tunel

    greeting = _Greeting()
    monkeypatch.setattr(connection, "mysql_greeting", greeting)
    monkeypatch.setattr(connection, "_wait_for_port", lambda *a, **k: arriba.set())

    class _Base:
        def ssm_tunnel(self, **kwargs):
            spawneados.append(kwargs)
            with greeting.lock:
                greeting.hay_tunel = True
            time.sleep(0.05)  # el túnel tarda en levantar
            return _Proc()

        def kill_ssm(self, pid):
            pass

    class _Proc:
        pid = 4242

        def poll(self):
            return None

        def terminate(self):
            pass

    class _Conn:
        def close(self):
            pass

    monkeypatch.setattr(connection, "BaseCommand", lambda: _Base())
    monkeypatch.setattr(connection, "_open", lambda *args, **kwargs: _Conn())

    class _Cfg:
        env = "dev"
        db_port = 8100
        profile = "base-profile"
        region = "us-west-2"

        def get(self, key, default=None):
            return {
                "AWS_INSTANCE": "i-1",
                "AWS_HOST": "db.example.com",
                "AWS_PORT": "53360",
                "AWS_REGION": "us-west-2",
                "AWS_USER": "app",
            }.get(key, default)

        def require(self, key):
            return self.get(key)

    errores = []

    def _conectar():
        try:
            with connection.connect(_Cfg()) as conn:
                conn.close()
        except Exception as exc:  # pragma: no cover - sólo para el assert
            errores.append(exc)

    hilos = [threading.Thread(target=_conectar) for _ in range(4)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(timeout=15)

    assert not errores, errores
    assert len(spawneados) == 1, f"se abrieron {len(spawneados)} túneles"
