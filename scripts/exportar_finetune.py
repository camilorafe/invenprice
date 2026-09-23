"""Exporta el dataset de la Fase 4 al formato de chat (system/user/assistant) para fine-tuning LoRA.

Salida: data/finetune_chat.jsonl — un mensaje por línea con el formato que aceptan Unsloth /
TRL SFTTrainer (`messages`). Ver docs/FINETUNING_LORA.md.

Uso:  python scripts/exportar_finetune.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from invenprice import copilot, finance as f  # noqa: E402

SALIDA = Path(__file__).resolve().parent.parent / "data" / "finetune_chat.jsonl"

SYSTEM = (
    "Eres un consultor de pricing para pequeñas empresas. Recibes un caso en JSON y devuelves SOLO un JSON con las "
    "claves precio_recomendado, margen_resultante_pct, justificacion y riesgo. El precio_recomendado nunca puede "
    "ser menor que el precio mínimo viable indicado. Cita los números exactos del caso."
)


def main() -> None:
    casos = copilot.cargar_dataset()
    with SALIDA.open("w", encoding="utf-8") as fh:
        for c in casos:
            i = c["input"]
            pmin = f.precio_minimo_viable(i["costo"], i["margen_minimo_pct"]).precio
            user = (
                "Entrada: " + json.dumps(i, ensure_ascii=False)
                + f"\nDato calculado: precio mínimo viable = {pmin:.2f}\nSalida:"
            )
            fh.write(json.dumps({
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": user},
                    {"role": "assistant", "content": json.dumps(c["output"], ensure_ascii=False)},
                ]
            }, ensure_ascii=False) + "\n")
    print(f"{len(casos)} ejemplos escritos en {SALIDA}")


if __name__ == "__main__":
    main()
