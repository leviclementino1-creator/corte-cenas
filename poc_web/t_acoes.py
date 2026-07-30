"""As ações de curadoria da Ponte, num fixture ISOLADO.

Remover e mover mexem no banco E nas pastas de verdade. Testar direto no
acervo do Levi mudaria a curadoria dele, então aqui tudo acontece numa cópia:
banco copiado, pasta de saída temporária, clipes copiados. Nada no
G:\\App Corte Cenas\\Output é tocado.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

RAIZ = Path(r"G:\App Corte Cenas")
sys.path.insert(0, str(RAIZ))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
from poc_web.ponte import Ponte  # noqa: E402


def monta_fixture(tmp: Path) -> tuple[Path, Path]:
    """Copia o banco e um pedaço do episódio (5 cenas) pra um lugar seguro."""
    banco = tmp / "index.db"
    shutil.copy2(RAIZ / "cache" / "index.db", banco)

    origem = RAIZ / "Output" / "Mushoku" / "S03E02"
    saida = tmp / "Output"
    destino = saida / "Mushoku" / "S03E02"
    (destino / "shots").mkdir(parents=True)
    (destino / "keyframes").mkdir(parents=True)

    con = sqlite3.connect(banco)
    con.row_factory = sqlite3.Row
    linhas = con.execute(
        "SELECT idx, file, keyframe FROM shot WHERE episode_id = 3 ORDER BY idx LIMIT 5"
    ).fetchall()
    for r in linhas:
        for rel in (r["file"], r["keyframe"]):
            if not rel:
                continue
            de, para = origem / rel, destino / rel
            if de.exists():
                shutil.copy2(de, para)
    # as pastas por personagem, como o app cria
    donos = con.execute(
        """SELECT s.idx, s.file, c.name FROM shot s
             JOIN shot_character sc ON sc.shot_id = s.id
             JOIN character c ON c.id = sc.character_id
            WHERE s.episode_id = 3 AND s.idx < 5"""
    ).fetchall()
    for d in donos:
        pasta = destino / "by_character" / d["name"]
        pasta.mkdir(parents=True, exist_ok=True)
        clipe = destino / d["file"]
        if clipe.exists():
            alvo = pasta / clipe.name
            if not alvo.exists():
                alvo.hardlink_to(clipe)
    con.close()
    return banco, saida


def conta_links(destino: Path) -> dict[str, int]:
    p = destino / "by_character"
    if not p.exists():
        return {}
    return {d.name: len(list(d.glob("*.mp4"))) for d in sorted(p.iterdir()) if d.is_dir()}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="cc_acoes_"))
    print(f"fixture em {tmp}\n")
    banco, saida = monta_fixture(tmp)
    destino = saida / "Mushoku" / "S03E02"

    p = Ponte(banco, destino)
    p._saida = lambda: saida            # a saída é a temporária, não a real
    cenas = json.loads(p.cenas_do_episodio("3"))
    print(f"episódio aberto: {len(cenas)} cenas · raiz = {p.raiz.name}")
    print(f"pastas antes : {conta_links(destino)}\n")

    def linha(n, texto):
        print(f"[{n}] {texto}")

    # --- 1. juntar --------------------------------------------------------
    r = json.loads(p.acao_cena(json.dumps({"acao": "juntar", "idxs": [1]})))
    linha(1, f"juntar cena 1: {r['msg']}")
    with sqlite3.connect(banco) as c:
        n = c.execute("SELECT COUNT(*) FROM shot_merge WHERE episode_id=3").fetchone()[0]
    linha(1, f"    linhas em shot_merge: {n}")

    # --- 2. desjuntar -----------------------------------------------------
    r = json.loads(p.acao_cena(json.dumps({"acao": "desjuntar", "idxs": [1]})))
    linha(2, f"desjuntar: {r['msg']}")
    with sqlite3.connect(banco) as c:
        n = c.execute("SELECT COUNT(*) FROM shot_merge WHERE episode_id=3").fetchone()[0]
    linha(2, f"    linhas em shot_merge: {n}  (tem que voltar pra 0)")

    # --- 3. remover -------------------------------------------------------
    alvo = next((c for c in cenas if c["quem"]), None)
    if alvo is None:
        print("nenhuma cena com dono no recorte — pulando remover/mover")
        return 0
    dono = alvo["quem"][0]
    linha(3, f"removendo a cena {alvo['idx']:04d} da pasta de {dono}")
    r = json.loads(p.acao_cena(json.dumps(
        {"acao": "remover", "idxs": [alvo["idx"]], "de": dono})))
    linha(3, f"    {r['msg']}")
    linha(3, f"    pastas agora: {conta_links(destino)}")
    with sqlite3.connect(banco) as c:
        bloq = c.execute(
            "SELECT COUNT(*) FROM manual_override WHERE episode_id=3 AND shot_idx=? "
            "AND action='block'", (alvo["idx"],)).fetchone()[0]
    linha(3, f"    memória de curadoria (block): {bloq}  (1 = a reanálise não devolve)")
    mestre = destino / "shots" / ("%04d.mp4" % alvo["idx"])
    linha(3, f"    clipe mestre continua em shots/: {mestre.exists()}")

    # --- 4. mover ---------------------------------------------------------
    p.cenas_do_episodio("3")            # recarrega o estado, como a UI faz
    outro = next((c for c in json.loads(p.cenas_do_episodio("3")) if c["quem"]), None)
    if outro is None:
        print("sem cena com dono pra mover")
        return 0
    de = outro["quem"][0]
    elenco = json.loads(p.elenco_do_anime())
    para = next((n for n in elenco if n != de), None)
    linha(4, f"movendo a cena {outro['idx']:04d}: {de} → {para}")
    r = json.loads(p.acao_cena(json.dumps(
        {"acao": "mover", "idxs": [outro["idx"]], "de": de, "para": para})))
    linha(4, f"    {r['msg']}")
    linha(4, f"    pastas agora: {conta_links(destino)}")

    # --- 5. apagar do acervo (lixeira, não rmtree) ------------------------
    antes_shots = destino / "shots"
    linha(5, f"apagando o episódio 3 do acervo (pasta existe: {antes_shots.exists()})")
    r = json.loads(p.apagar_do_acervo(json.dumps({"ids": [3]})))
    linha(5, f"    {r['msg']}")
    lixeira = saida / "_lixeira"
    achados = list(lixeira.rglob("*.mp4")) if lixeira.exists() else []
    linha(5, f"    pasta original sumiu: {not destino.exists()}")
    linha(5, f"    clipes na lixeira: {len(achados)}  (soft-delete, não rmtree)")
    with sqlite3.connect(banco) as c:
        n_shot = c.execute("SELECT COUNT(*) FROM shot WHERE episode_id=3").fetchone()[0]
        n_ep = c.execute("SELECT COUNT(*) FROM episode WHERE id=3").fetchone()[0]
        n_char = c.execute("SELECT COUNT(*) FROM character").fetchone()[0]
    linha(5, f"    banco: shots={n_shot} episodio={n_ep} personagens={n_char} (personagens ficam)")

    # --- 6. o banco real continua intacto? --------------------------------
    with sqlite3.connect(RAIZ / "cache" / "index.db") as c:
        n_real = c.execute("SELECT COUNT(*) FROM shot_merge").fetchone()[0]
        m_real = c.execute("SELECT COUNT(*) FROM manual_override").fetchone()[0]
    linha(5, f"banco REAL intocado: shot_merge={n_real} manual_override={m_real}")

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nfixture apagado. Nada em {RAIZ / 'Output'} foi tocado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
