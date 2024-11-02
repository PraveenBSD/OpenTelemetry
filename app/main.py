from fastapi import FastAPI, HTTPException, Depends
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from opentelemetry.sdk.resources import Resource
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.jaeger.thrift import JaegerExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
import os

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@postgres:5432/mydb")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# OpenTelemetry setup
trace.set_tracer_provider(
    TracerProvider(
        resource=Resource.create({"service.name": "fastapi_service"})
    )
)
tracer = trace.get_tracer(__name__)

jaeger_exporter = JaegerExporter(
    agent_host_name=os.getenv("OTEL_EXPORTER_JAEGER_AGENT_HOST", "jaeger"),
    agent_port=6831,
)

span_processor = BatchSpanProcessor(jaeger_exporter)
trace.get_tracer_provider().add_span_processor(span_processor)

# FastAPI setup with OpenTelemetry
app = FastAPI()
FastAPIInstrumentor.instrument_app(app)
SQLAlchemyInstrumentor().instrument(engine=engine)

# Database model
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    email = Column(String, unique=True, index=True)

# Create tables
Base.metadata.create_all(bind=engine)

# Dependency to get a database session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# API routes
@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/users")
def get_users(db: SessionLocal = Depends(get_db)):
    with tracer.start_as_current_span("get_users"):
        users = db.query(User).all()
        return [{"id": user.id, "name": user.name, "email": user.email} for user in users]

@app.get("/users/{user_id}")
def read_user(user_id: int, db: SessionLocal = Depends(get_db)):
    with tracer.start_as_current_span("read_user"):
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        return {"id": user.id, "name": user.name, "email": user.email}

@app.post("/users")
def create_user(name: str, email: str, db: SessionLocal = Depends(get_db)):
    with tracer.start_as_current_span("create_user"):
        db_user = User(name=name, email=email)
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        return {"id": db_user.id, "name": db_user.name, "email": db_user.email}

# Startup event to prefill the database
@app.on_event("startup")
def prefill_users():
    db = SessionLocal()
    try:
        if not db.query(User).first():  # Check if there are any users
            users = [
                User(name="Alice", email="alice@example.com"),
                User(name="Bob", email="bob@example.com"),
                User(name="Charlie", email="charlie@example.com"),
            ]
            db.add_all(users)
            db.commit()
    finally:
        db.close()
