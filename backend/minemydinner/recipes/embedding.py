from sqlalchemy import select
from sqlalchemy.orm import Session

from minemydinner.llm.client import AllModelsFailed, LLMClient, MonthlyCapExceeded
from minemydinner.models import Recipe


def embedding_text(recipe: Recipe) -> str:
    names = ", ".join(line.name for line in recipe.ingredients)
    parts = [recipe.title, names, ", ".join(recipe.tags), recipe.steps_summary or ""]
    return "\n".join(part for part in parts if part)


def try_embed_recipe(session: Session, client: LLMClient, recipe: Recipe) -> bool:
    """Embed and commit. A model failure leaves the embedding null for a later retry."""
    try:
        recipe.embedding = client.embed([embedding_text(recipe)])[0]
    except (AllModelsFailed, MonthlyCapExceeded):
        return False
    session.commit()
    return True


def retry_pending_embeddings(session: Session, client: LLMClient) -> int:
    """Embed every recipe still without one. Returns how many succeeded."""
    pending = session.scalars(
        select(Recipe).where(Recipe.embedding.is_(None)).order_by(Recipe.id)
    ).all()
    embedded = 0
    for recipe in pending:
        try:
            recipe.embedding = client.embed([embedding_text(recipe)])[0]
        except MonthlyCapExceeded:
            break
        except AllModelsFailed:
            continue
        session.commit()
        embedded += 1
    return embedded
