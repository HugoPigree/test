import sys
import types

from autoclip import quality
from autoclip.config import Config
from autoclip.subtitles import Word


def cand(name="a", score=15.0, t=1000.0, mesures=None, viewers=250, channel="jltomy", connexion=None):
    chat = [{"ts": t, "user": f"v{i}", "msg": "KEKW"} for i in range(viewers)]
    return {"name": name, "channel": channel, "score": score, "t_peak_abs": t, "chat": chat,
            "duration": 40.0, "connexion_debut": connexion,
            "mesures": mesures if mesures is not None else
            {"duree": 40.0, "voix_db": 12.0, "silence": 0.1, "mots": 100, "mots_par_s": 2.5,
             "debut_phrase": True}}


def test_perfect_candidate_scores_100_with_detail():
    q = quality.score(Config(), cand())
    assert q["total"] == 100 and q["penalites"] == {}
    assert set(q["signaux"]) == {"chat", "viewers", "voix", "parole", "phrase"}
    assert q["signaux"]["voix"] == {"valeur": "+12.0 dB au moment fort", "points": 20, "max": 20}
    line = quality.summary(q)
    assert "chat 25/25" in line and "phrase 20/20 (début de phrase)" in line


def test_signals_are_proportional_and_capped():
    c = cand(score=7.5, viewers=50, mesures={"duree": 40.0, "voix_db": 30.0, "mots_par_s": 0.5,
                                             "mots": 20, "debut_phrase": False, "silence": 0.0})
    s = quality.score(Config(), c)["signaux"]
    assert s["chat"]["points"] == 12.5 and s["viewers"]["points"] == 3.0
    assert s["voix"]["points"] == 20  # plafonné
    assert s["parole"]["points"] == 4.0 and s["phrase"]["points"] == 0.0


def test_penalties():
    cfg = Config()
    c = cand(t=10_000.0, connexion=10_000.0 - 120,
             mesures={"duree": 70.0, "voix_db": 12.0, "silence": 0.8, "mots": 2, "mots_par_s": 0.1,
                      "debut_phrase": True})
    better = cand(name="b", t=10_000.0 + 300)  # parfaite, 5 min plus tard, même chaîne
    q = quality.score(cfg, c, [c, better])
    assert set(q["penalites"]) == {"duree", "silence", "debut_live", "pause", "proximite"}
    assert q["penalites"]["proximite"]["points"] == -cfg.quality.penalty_nearby
    assert "(b)" in q["penalites"]["proximite"]["valeur"]
    assert q["total"] == max(0, round(q["base"] - 20 - 15 - 15 - 25 - 10))
    # La meilleure n'est pas pénalisée par la moins bonne, ni une autre chaîne.
    assert "proximite" not in quality.score(cfg, better, [c, better])["penalites"]
    other = cand(name="c", t=10_000.0, channel="etostark__", mesures=c["mesures"])
    assert "proximite" not in quality.score(cfg, other, [better, other])["penalites"]


def test_missing_measures_say_inconnu():
    q = quality.score(Config(), cand(mesures={}))
    assert q["signaux"]["voix"]["valeur"] == "inconnu" and q["signaux"]["parole"]["points"] == 0


def test_external_entry_point(monkeypatch):
    mod = types.ModuleType("ia_test")
    mod.noter = lambda c, m: {"points": 0.5, "detail": "drôle"}
    monkeypatch.setitem(sys.modules, "ia_test", mod)
    cfg = Config()
    cfg.quality.external, cfg.quality.weight_external = "ia_test:noter", 20
    q = quality.score(cfg, cand())
    assert q["signaux"]["externe"] == {"valeur": "drôle", "points": 10.0, "max": 20}
    assert q["total"] == round(100 * 110 / 120)
    cfg.quality.external = "module_absent:f"  # IA en panne : ignorée, pas d'erreur
    assert "externe" not in quality.score(cfg, cand())["signaux"]


def test_clip_start_on_a_sentence():
    words = [Word(0.0, 0.5, "Bon"), Word(0.5, 1.0, "voilà."), Word(1.2, 1.6, "Alors"),
             Word(1.6, 2.0, "regarde"), Word(3.5, 4.0, "Ensuite")]
    assert quality.starts_sentence(words, 1.1)       # juste après « voilà. »
    assert not quality.starts_sentence(words, 1.8)   # coupe « regarde » en deux
    assert not quality.starts_sentence(words, 1.6)   # entre deux mots d'une même phrase
    assert quality.starts_sentence(words, 3.0)       # dans un silence de 1,5 s
    assert quality.starts_sentence([], 2.0)
