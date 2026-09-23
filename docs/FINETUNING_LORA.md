# Fine-tuning ligero (LoRA) del copiloto local — guía para ejecutar gratis

> **Estado:** no se ejecutó en la sesión de desarrollo (la máquina de desarrollo no tiene GPU).
> Todo lo necesario está preparado para correrlo después, sin costo, en Google Colab (tier gratuito,
> GPU T4 de 16 GB). El sistema funciona igual sin este paso: el few-shot embebido en el prompt ya
> usa el dataset, y el motor de reglas es el fallback permanente.

## Por qué LoRA y no fine-tuning completo

- LoRA entrena ~1 % de los parámetros (adaptadores de rango bajo), cabe en una T4 gratuita con el
  modelo cargado en 4 bits (QLoRA) y termina en 10-20 minutos con 48 ejemplos.
- El resultado es un archivo pequeño (decenas de MB) que se fusiona en un GGUF y se importa a
  Ollama con un `Modelfile`. Costo recurrente: $0.

## Paso 0 — Exportar el dataset al formato de chat

```bash
python scripts/exportar_finetune.py
# -> data/finetune_chat.jsonl  (48 líneas, formato {"messages":[system,user,assistant]})
```

Sube ese archivo a Colab (o a Google Drive).

## Paso 1 — Notebook de Colab (Unsloth, QLoRA 4-bit)

Crear un notebook con runtime **GPU T4** y ejecutar celda a celda:

```python
# Celda 1 — dependencias
!pip install -q unsloth trl datasets

# Celda 2 — modelo base en 4 bits
from unsloth import FastLanguageModel
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="unsloth/Qwen2.5-7B-Instruct-bnb-4bit",   # alternativa ligera: unsloth/Qwen2.5-3B-Instruct-bnb-4bit
    max_seq_length=4096,
    load_in_4bit=True,
)
model = FastLanguageModel.get_peft_model(
    model, r=16, lora_alpha=16, lora_dropout=0,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    use_gradient_checkpointing="unsloth",
)

# Celda 3 — dataset
from datasets import load_dataset
ds = load_dataset("json", data_files="finetune_chat.jsonl", split="train")
def to_text(ej):
    return {"text": tokenizer.apply_chat_template(ej["messages"], tokenize=False)}
ds = ds.map(to_text)

# Celda 4 — entrenamiento (≈10-15 min en T4)
from trl import SFTTrainer, SFTConfig
trainer = SFTTrainer(
    model=model, tokenizer=tokenizer, train_dataset=ds, dataset_text_field="text",
    args=SFTConfig(
        per_device_train_batch_size=2, gradient_accumulation_steps=4,
        num_train_epochs=3, learning_rate=2e-4, lr_scheduler_type="cosine",
        warmup_steps=5, logging_steps=1, output_dir="salida", seed=42, fp16=True,
    ),
)
trainer.train()

# Celda 5 — exportar a GGUF cuantizado (Q4_K_M) listo para Ollama
model.save_pretrained_gguf("invenprice-qwen", tokenizer, quantization_method="q4_k_m")
```

Descargar `invenprice-qwen/*.gguf` (≈4,7 GB para 7B; ≈2 GB para 3B).

## Paso 2 — Importar a Ollama en la máquina del negocio

`Modelfile`:

```
FROM ./invenprice-qwen-Q4_K_M.gguf
PARAMETER temperature 0.2
SYSTEM Eres un consultor de pricing para pequeñas empresas. Respondes SOLO con JSON con las claves precio_recomendado, margen_resultante_pct, justificacion y riesgo.
```

```bash
ollama create invenprice-qwen -f Modelfile
```

Luego en InvenPrice: **Configuración → modelo local** = `invenprice-qwen` (clave `modelo_local` de la
tabla `configuracion`). No hace falta cambiar código.

## Paso 3 — Verificar

```bash
python scripts/benchmark_llm.py --modelo invenprice-qwen
```

Comparar la columna *JSON válido* y el desvío frente a *precio dataset* con el benchmark del
modelo base en `docs/benchmark.md`. Si el modelo fine-tuneado produce menos salidas válidas, no
usarlo: el sistema seguirá funcionando con el motor de reglas en cualquier caso.

## Advertencias

- 48 ejemplos es poco: 3 épocas con `r=16` bastan; más épocas sobreajustan y el modelo empieza a
  copiar justificaciones de memoria. Ampliar el dataset (con casos reales del negocio anonimizados)
  antes de subir épocas.
- El guardrail de precio mínimo y la validación de schema **siguen activos** con el modelo
  fine-tuneado. El fine-tuning mejora la calidad del texto, no reemplaza las garantías matemáticas.
