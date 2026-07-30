"""A escolha da pasta do anime — a regra que impede duas pastas do mesmo show.

    python -m unittest discover tests -v

Usa `unittest` (stdlib) porque o projeto não tem pytest instalado.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.storage import pastas  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="cc_pastas_"))
        self.cache = self.tmp / "cache"

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def saida(self, *nomes: str) -> Path:
        s = self.tmp / "Output"
        s.mkdir(exist_ok=True)
        for n in nomes:
            (s / n).mkdir(exist_ok=True)
        return s


class TestEscolha(Base):
    def test_sem_nada_usa_o_nome_digitado(self):
        p = pastas.resolver("Dr. Stone", self.saida(), self.cache)
        self.assertEqual(p.name, "Dr. Stone")

    def test_pasta_com_o_mesmo_nome_e_reusada(self):
        s = self.saida("Dr. Stone")
        self.assertEqual(pastas.resolver("Dr. Stone", s, self.cache), s / "Dr. Stone")

    def test_pergunta_quando_existe_parecida(self):
        """'Mushoku Tensei' digitado com a pasta 'Mushoku' no disco."""
        s = self.saida("Mushoku")
        visto = {}

        def perguntar(digitado, candidatas):
            visto["candidatas"] = candidatas
            return candidatas[0]

        p = pastas.resolver("Mushoku Tensei", s, self.cache, perguntar)
        self.assertEqual(p, s / "Mushoku")
        self.assertEqual(visto["candidatas"], ["Mushoku"])

    def test_so_pergunta_uma_vez(self):
        """A segunda análise do mesmo nome não pode perguntar de novo."""
        s = self.saida("Mushoku")
        n = {"vezes": 0}

        def perguntar(digitado, candidatas):
            n["vezes"] += 1
            return "Mushoku"

        pastas.resolver("Mushoku Tensei", s, self.cache, perguntar)
        pastas.resolver("Mushoku Tensei", s, self.cache, perguntar)
        pastas.resolver("mushoku   tensei", s, self.cache, perguntar)  # outra escrita
        self.assertEqual(n["vezes"], 1)

    def test_recusar_cria_a_propria(self):
        s = self.saida("Mushoku")
        p = pastas.resolver("Mushoku Tensei", s, self.cache, lambda d, c: None)
        self.assertEqual(p, s / "Mushoku Tensei")

    def test_sem_perguntador_nao_trava(self):
        """Análise em lote e teste automático não podem esperar resposta."""
        s = self.saida("Mushoku")
        self.assertEqual(
            pastas.resolver("Mushoku Tensei", s, self.cache), s / "Mushoku Tensei"
        )


class TestParecidas(Base):
    def test_nao_casa_por_prefixo_solto(self):
        """'Mush' não pode arrastar 'Mushoku' junto."""
        s = self.saida("Mushoku")
        self.assertEqual(pastas.parecidas("Mush", s), [])
        self.assertEqual(pastas.parecidas("Naruto", s), [])

    def test_casa_nos_dois_sentidos(self):
        s = self.saida("Mushoku Tensei")
        self.assertEqual(pastas.parecidas("Mushoku", s), ["Mushoku Tensei"])
        s2 = self.tmp / "b" / "Output"
        s2.mkdir(parents=True)
        (s2 / "Mushoku").mkdir()
        self.assertEqual(pastas.parecidas("Mushoku Tensei", s2), ["Mushoku"])

    def test_ignora_a_lixeira(self):
        s = self.saida("_lixeira", "Mushoku")
        self.assertNotIn("_lixeira", pastas.parecidas("Mushoku", s))

    def test_saida_inexistente_nao_explode(self):
        self.assertEqual(pastas.parecidas("Mushoku", self.tmp / "nao_existe"), [])


class TestMemoria(Base):
    def test_apontar_na_mao(self):
        """Usuário arrumou as pastas fora do app: a próxima análise segue."""
        s = self.saida("Mushoku")
        oficial = "Mushoku Tensei III: Isekai Ittara Honki Dasu"
        pastas.apontar(oficial, "Mushoku", self.cache)
        self.assertEqual(pastas.resolver(oficial, s, self.cache), s / "Mushoku")

    def test_memoria_e_json_legivel(self):
        pastas.resolver("Dr. Stone", self.saida(), self.cache)
        d = json.loads((self.cache / pastas.ARQUIVO).read_text(encoding="utf-8"))
        self.assertIn("Dr. Stone", d.values())

    def test_json_corrompido_nao_derruba_a_analise(self):
        self.cache.mkdir(parents=True, exist_ok=True)
        (self.cache / pastas.ARQUIVO).write_text("{ isto não é json", encoding="utf-8")
        p = pastas.resolver("Dr. Stone", self.saida(), self.cache)
        self.assertEqual(p.name, "Dr. Stone")


if __name__ == "__main__":
    unittest.main(verbosity=2)
