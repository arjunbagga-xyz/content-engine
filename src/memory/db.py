import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, Enum
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from src.core.config import config

Base = declarative_base()

class Character(Base):
    __tablename__ = "characters"
    
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    status = Column(String, default="active")  # active, inactive
    role = Column(String, nullable=False)
    personality = Column(Text, nullable=False)
    visual_keywords = Column(Text, nullable=False)
    voice = Column(String, nullable=False)
    themes = Column(Text, nullable=False)  # JSON-encoded string
    reel_style = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    posts = relationship("ContentPost", back_populates="character")
    events = relationship("NarrativeEvent", back_populates="character")
    arcs = relationship("ArcSummary", back_populates="character")

class NarrativeEvent(Base):
    __tablename__ = "narrative_events"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(String, ForeignKey("characters.id"), nullable=False)
    event_description = Column(Text, nullable=False)
    importance = Column(Integer, default=5)  # 1-10 scale
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    character = relationship("Character", back_populates="events")

class ArcSummary(Base):
    __tablename__ = "arc_summaries"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(String, ForeignKey("characters.id"), nullable=False)
    summary_text = Column(Text, nullable=False)
    week_start = Column(DateTime, nullable=False)
    week_end = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    character = relationship("Character", back_populates="arcs")

class ContentPost(Base):
    __tablename__ = "content_queue"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(String, ForeignKey("characters.id"), nullable=False)
    platform = Column(String, nullable=False)  # instagram, x
    post_type = Column(String, nullable=False)  # static, carousel, tweet, reel, thread
    state = Column(String, default="planned")  # planned, scripted, generating, staged, publishing, published, failed, held
    scheduled_time = Column(DateTime, nullable=False)
    pid = Column(Integer, nullable=True)        # heartbeat: pid of running generate/publish job (NULL when idle)
    heartbeat_at = Column(DateTime, nullable=True)
    actual_posted_time = Column(DateTime, nullable=True)
    caption = Column(Text, nullable=True)
    script = Column(Text, nullable=True)
    media_path = Column(String, nullable=True)  # Path to generated image/video
    image_prompt = Column(Text, nullable=True)
    platform_post_id = Column(String, nullable=True)  # Store IG/X post ID after publishing
    media_type = Column(String, nullable=True)  # photo, video, carousel, text
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)
    tone_plan = Column(Text, nullable=True)          # JSON: per-turn emotion sequence for the debate
    topic = Column(Text, nullable=True)              # resolved concrete subtopic (from subtopic LLM)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    character = relationship("Character", back_populates="posts")


class ScheduledJob(Base):
    """Audit + dispatch ledger written by the planner.

    PLANNER vs DISPATCHER (decoupled):
      * The PLANNER (6x/day) is the ONLY thing that CREATES/deletes these rows. It
        decides what to make (subtopic via LLM), how many (posts_per_day per account),
        and when (fire_at spread across the day). It writes:
          - one 'dispatch' row  -> invokes the dispatcher drainer for a batch
          - N 'generate' rows    -> status 'queued' (consumed by the dispatcher)
          - N 'publish' rows     -> status 'queued', fire_at spread through the day
      * The DISPATCHER runs ONE-SHOT when a 'dispatch' row fires. It drains the batch:
        generates queued posts one-by-one, and on each 'staged' it arms that post's
        publish at its planned fire_at. It is NOT a daemon and does NOT poll.

    Status values:
      pending  - a standalone job waiting to be fired by the old fire_due path (legacy)
      queued   - waiting inside a batch, to be consumed by the dispatcher
      fired    - dispatcher picked it up / spawned
      done     - completed successfully
      failed   - exhausted retries
      missed   - was 'fired' but heartbeat died (planner reconcile reclaims it)
    """
    __tablename__ = "scheduled_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(Integer, ForeignKey("content_queue.id"), nullable=False)
    character_id = Column(String, nullable=False)
    step = Column(String, nullable=False)            # 'generate' | 'publish' | 'dispatch'
    fire_at = Column(DateTime, nullable=False)        # UTC; when it should run
    argv = Column(Text, nullable=False)              # JSON list of exact args
    batch = Column(String, nullable=True)            # batch key, e.g. 'tate_vs_peppa:2026-08-29'
    status = Column(String, default="queued")        # queued, pending, fired, done, failed, missed
    retry_count = Column(Integer, default=0)         # times a dispatch has re-queued this job
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    post = relationship("ContentPost")

# Setup Database
engine = create_engine(f"sqlite:///{config.SQLITE_DB_PATH}", echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)
    # SQLite can't ALTER ADD inside create_all for pre-existing tables — add new
    # columns defensively so an existing DB picks up schema changes without a wipe.
    from sqlalchemy import inspect as _inspect, text as _text
    cols = {c["name"] for c in _inspect(engine).get_columns("content_queue")}
    for col, ddl in [
        ("tone_plan", "TEXT"),
        ("topic", "TEXT"),
    ]:
        if col not in cols:
            with engine.begin() as conn:
                conn.execute(_text(f"ALTER TABLE content_queue ADD COLUMN {col} {ddl}"))
    jcols = {c["name"] for c in _inspect(engine).get_columns("scheduled_jobs")}
    if "batch" not in jcols:
        with engine.begin() as conn:
            conn.execute(_text("ALTER TABLE scheduled_jobs ADD COLUMN batch TEXT"))

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
