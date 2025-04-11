from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from enum import Enum
import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database connection - Modified for Render
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if not DATABASE_URL:
    DATABASE_URL = "postgresql://postgres:postgres@localhost/autolog"

# Print connection string for debugging (remove in production)
print(f"Connecting to database: {DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else 'local'}")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create FastAPI app
app = FastAPI(title="AutoLog API", description="API for AutoLog parking system")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Enums
class VehicleType(str, Enum):
    bike = "bike"
    scooter = "scooter"
    car = "car"
    truck = "truck"
    others = "others"

class UserRole(str, Enum):
    admin = "admin"
    operator = "operator"
    viewer = "viewer"

# Models
class VehicleRecordBase(BaseModel):
    license_plate: str
    vehicle_type: VehicleType

class VehicleRecordCreate(VehicleRecordBase):
    pass

class VehicleRecordUpdate(BaseModel):
    exit_time: Optional[datetime] = None

class VehicleRecord(VehicleRecordBase):
    id: int
    entry_time: datetime
    exit_time: Optional[datetime] = None
    parking_fee: Optional[float] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class UserBase(BaseModel):
    username: str
    email: EmailStr
    role: UserRole

class UserCreate(UserBase):
    password: str

class User(UserBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        
class UserAuth(BaseModel):
    username: str
    role: UserRole
    
    class Config:
        from_attributes = True

class ParkingRateBase(BaseModel):
    vehicle_type: VehicleType
    hourly_rate: float

class ParkingRate(ParkingRateBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class ParkingSpaceBase(BaseModel):
    vehicle_type: VehicleType
    total_spaces: int

class ParkingSpace(ParkingSpaceBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class DashboardData(BaseModel):
    vehicle_type: VehicleType
    total_spaces: int
    occupied_spaces: int
    available_spaces: int
    daily_revenue: float

class RealtimeParkingActivity(BaseModel):
    license_plate: str
    vehicle_type: VehicleType
    entry_time: datetime
    exit_time: Optional[datetime] = None
    duration_minutes: int

class AnalyticsRequest(BaseModel):
    start_date: date
    end_date: date
    vehicle_type: Optional[VehicleType] = None
    min_duration_hours: Optional[float] = None
    max_duration_hours: Optional[float] = None

class AnalyticsResponse(BaseModel):
    total_vehicles: int
    avg_duration_hours: float
    peak_hour: int
    total_revenue: float

# Helper functions
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Root route for health check
@app.get("/")
async def root():
    return {"status": "ok", "message": "AutoLog API is running"}

# Vehicle routes
@app.get("/vehicles", response_model=List[VehicleRecord])
async def get_vehicles(
    license_plate: Optional[str] = None, 
    vehicle_type: Optional[VehicleType] = None,
    start_date: Optional[date] = None, 
    end_date: Optional[date] = None,
    skip: int = 0, 
    limit: int = 100
):
    db = SessionLocal()
    
    # Build query
    query_str = "SELECT * FROM vehicle_records WHERE 1=1"
    params = {}
    
    if license_plate:
        query_str += " AND license_plate = :license_plate"
        params["license_plate"] = license_plate
    
    if vehicle_type:
        query_str += " AND vehicle_type = :vehicle_type"
        params["vehicle_type"] = vehicle_type
    
    if start_date:
        query_str += " AND DATE(entry_time) >= :start_date"
        params["start_date"] = start_date
    
    if end_date:
        query_str += " AND DATE(entry_time) <= :end_date"
        params["end_date"] = end_date
    
    query_str += " ORDER BY entry_time DESC LIMIT :limit OFFSET :skip"
    params["limit"] = limit
    params["skip"] = skip
    
    result = db.execute(text(query_str), params).fetchall()
    db.close()
    
    return [dict(zip(["id", "license_plate", "vehicle_type", "entry_time", "exit_time", 
                      "parking_fee", "created_at", "updated_at"], row)) for row in result]

@app.post("/vehicles", response_model=VehicleRecord)
async def create_vehicle_record(vehicle: VehicleRecordCreate):
    db = SessionLocal()
    
    # Insert vehicle record
    query = text("""
        INSERT INTO vehicle_records (license_plate, vehicle_type)
        VALUES (:license_plate, :vehicle_type)
        RETURNING *
    """)
    
    result = db.execute(query, {
        "license_plate": vehicle.license_plate,
        "vehicle_type": vehicle.vehicle_type
    }).fetchone()
    
    db.commit()
    db.close()
    
    if not result:
        raise HTTPException(status_code=400, detail="Failed to create vehicle record")
    
    return dict(zip(["id", "license_plate", "vehicle_type", "entry_time", "exit_time", 
                     "parking_fee", "created_at", "updated_at"], result))

@app.put("/vehicles/{license_plate}/exit", response_model=VehicleRecord)
async def vehicle_exit(license_plate: str):
    db = SessionLocal()
    
    # Calculate parking fee
    fee_query = text("SELECT calculate_parking_fee(:license_plate)")
    parking_fee = db.execute(fee_query, {"license_plate": license_plate}).scalar()
    
    # Update vehicle record
    query = text("""
        UPDATE vehicle_records
        SET exit_time = NOW(), parking_fee = :parking_fee
        WHERE license_plate = :license_plate AND exit_time IS NULL
        RETURNING *
    """)
    
    result = db.execute(query, {
        "license_plate": license_plate,
        "parking_fee": parking_fee
    }).fetchone()
    
    db.commit()
    db.close()
    
    if not result:
        raise HTTPException(status_code=404, detail="No active parking record found for this license plate")
    
    return dict(zip(["id", "license_plate", "vehicle_type", "entry_time", "exit_time", 
                     "parking_fee", "created_at", "updated_at"], result))

# User management routes
@app.get("/users", response_model=List[User])
async def get_users(
    skip: int = 0, 
    limit: int = 100
):
    db = SessionLocal()
    query = text("SELECT * FROM users ORDER BY id LIMIT :limit OFFSET :skip")
    result = db.execute(query, {"limit": limit, "skip": skip}).fetchall()
    db.close()
    
    return [dict(zip(["id", "username", "email", "password", "role", 
                      "created_at", "updated_at"], row)) for row in result]

# Login verification endpoint
@app.get("/users/auth", response_model=UserAuth)
async def get_user_auth(username: str, password: str):
    db = SessionLocal()
    query = text("""
        SELECT username, role FROM users 
        WHERE username = :username AND password = :password
    """)
    
    result = db.execute(query, {
        "username": username,
        "password": password
    }).fetchone()
    
    db.close()
    
    if not result:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    return dict(zip(["username", "role"], result))

@app.post("/users", response_model=User)
async def create_user(user: UserCreate):
    db = SessionLocal()
    
    # Check if username already exists
    check_query = text("SELECT 1 FROM users WHERE username = :username OR email = :email")
    existing = db.execute(check_query, {"username": user.username, "email": user.email}).fetchone()
    
    if existing:
        raise HTTPException(status_code=400, detail="Username or email already registered")
    
    # Insert user
    query = text("""
        INSERT INTO users (username, email, password, role)
        VALUES (:username, :email, :password, :role)
        RETURNING *
    """)
    
    result = db.execute(query, {
        "username": user.username,
        "email": user.email,
        "password": user.password,
        "role": user.role
    }).fetchone()
    
    db.commit()
    db.close()
    
    return dict(zip(["id", "username", "email", "password", "role", 
                    "created_at", "updated_at"], result))

@app.put("/users/{user_id}", response_model=User)
async def update_user(
    user_id: int, 
    user: UserCreate
):
    db = SessionLocal()
    
    # Update user
    query = text("""
        UPDATE users
        SET username = :username, email = :email, password = :password, role = :role
        WHERE id = :user_id
        RETURNING *
    """)
    
    result = db.execute(query, {
        "user_id": user_id,
        "username": user.username,
        "email": user.email,
        "password": user.password,
        "role": user.role
    }).fetchone()
    
    db.commit()
    db.close()
    
    if not result:
        raise HTTPException(status_code=404, detail="User not found")
    
    return dict(zip(["id", "username", "email", "password", "role", 
                    "created_at", "updated_at"], result))

# Dashboard routes
@app.get("/dashboard", response_model=Dict[str, Any])
async def get_dashboard(
    date_param: Optional[date] = Query(None, alias="date")
):
    db = SessionLocal()
    
    # Get dashboard data
    target_date = date_param or datetime.now().date()
    query = text("SELECT * FROM get_dashboard_data(:date)")
    result = db.execute(query, {"date": target_date}).fetchall()
    
    # Get realtime parking activity
    activity_query = text("SELECT * FROM get_realtime_parking_activity()")
    activity_result = db.execute(activity_query).fetchall()
    
    db.close()
    
    # Process dashboard data
    dashboard_data = []
    total_spaces = 0
    total_occupied = 0
    total_revenue = 0
    
    for row in result:
        item = dict(zip(["vehicle_type", "total_spaces", "occupied_spaces", 
                        "available_spaces", "daily_revenue"], row))
        dashboard_data.append(item)
        total_spaces += item["total_spaces"]
        total_occupied += item["occupied_spaces"]
        total_revenue += item["daily_revenue"]
    
    # Process activity data
    activity_data = []
    for row in activity_result:
        item = dict(zip(["license_plate", "vehicle_type", "entry_time", 
                        "exit_time", "duration_minutes"], row))
        activity_data.append(item)
    
    return {
        "date": target_date,
        "summary": {
            "total_spaces": total_spaces,
            "occupied_spaces": total_occupied,
            "available_spaces": total_spaces - total_occupied,
            "occupancy_rate": round(total_occupied / total_spaces * 100, 2) if total_spaces > 0 else 0,
            "total_revenue": total_revenue
        },
        "by_vehicle_type": dashboard_data,
        "realtime_activity": activity_data
    }

# Analytics routes
@app.post("/analytics", response_model=AnalyticsResponse)
async def get_analytics(
    request: AnalyticsRequest
):
    db = SessionLocal()
    
    query = text("""
        SELECT * FROM get_analytics_data(
            :start_date, :end_date, :vehicle_type, 
            :min_duration_hours, :max_duration_hours
        )
    """)
    
    result = db.execute(query, {
        "start_date": request.start_date,
        "end_date": request.end_date,
        "vehicle_type": request.vehicle_type,
        "min_duration_hours": request.min_duration_hours,
        "max_duration_hours": request.max_duration_hours
    }).fetchone()
    
    db.close()
    
    if not result:
        return {
            "total_vehicles": 0,
            "avg_duration_hours": 0,
            "peak_hour": 0,
            "total_revenue": 0
        }
    
    return dict(zip(["total_vehicles", "avg_duration_hours", "peak_hour", "total_revenue"], result))

# Parking rates routes
@app.get("/parking-rates", response_model=List[ParkingRate])
async def get_parking_rates():
    db = SessionLocal()
    query = text("SELECT * FROM parking_rates ORDER BY vehicle_type")
    result = db.execute(query).fetchall()
    db.close()
    
    return [dict(zip(["id", "vehicle_type", "hourly_rate", "created_at", "updated_at"], row)) 
            for row in result]

@app.put("/parking-rates/{vehicle_type}", response_model=ParkingRate)
async def update_parking_rate(
    vehicle_type: VehicleType, 
    rate: ParkingRateBase
):
    db = SessionLocal()
    
    query = text("""
        UPDATE parking_rates
        SET hourly_rate = :hourly_rate
        WHERE vehicle_type = :vehicle_type
        RETURNING *
    """)
    
    result = db.execute(query, {
        "vehicle_type": vehicle_type,
        "hourly_rate": rate.hourly_rate
    }).fetchone()
    
    db.commit()
    db.close()
    
    if not result:
        raise HTTPException(status_code=404, detail="Parking rate not found")
    
    return dict(zip(["id", "vehicle_type", "hourly_rate", "created_at", "updated_at"], result))

# Parking spaces routes
@app.get("/parking-spaces", response_model=List[ParkingSpace])
async def get_parking_spaces():
    db = SessionLocal()
    query = text("SELECT * FROM parking_spaces ORDER BY vehicle_type")
    result = db.execute(query).fetchall()
    db.close()
    
    return [dict(zip(["id", "vehicle_type", "total_spaces", "created_at", "updated_at"], row)) 
            for row in result]

@app.put("/parking-spaces/{vehicle_type}", response_model=ParkingSpace)
async def update_parking_space(
    vehicle_type: VehicleType, 
    space: ParkingSpaceBase
):
    db = SessionLocal()
    
    query = text("""
        UPDATE parking_spaces
        SET total_spaces = :total_spaces
        WHERE vehicle_type = :vehicle_type
        RETURNING *
    """)
    
    result = db.execute(query, {
        "vehicle_type": vehicle_type,
        "total_spaces": space.total_spaces
    }).fetchone()
    
    db.commit()
    db.close()
    
    if not result:
        raise HTTPException(status_code=404, detail="Parking space not found")
    
    return dict(zip(["id", "vehicle_type", "total_spaces", "created_at", "updated_at"], result))

# For Render deployment, add this to make the app run properly
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)