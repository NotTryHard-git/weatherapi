import subprocess
import sys
import importlib.util

# Функция для проверки и установки пакетов
def install_and_import(package):
    try:
        spec = importlib.util.find_spec(package)
        if spec is None:
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])
    except Exception as e:
        sys.exit(1)

required_packages = ["fastapi", "httpx", "uvicorn"]

for package in required_packages:
    install_and_import(package)

import asyncio
from datetime import datetime
from typing import Dict, Optional, List
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

# Константы
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
UPDATE_INTERVAL = 900  


class WeatherData(BaseModel):
    temperature: float = None
    humidity: float = None
    pressure: float = None
    wind_speed: float = None
    precipitation: float = None


class City(BaseModel):
    name: str
    latitude: float
    longitude: float
    last_update: Optional[datetime] = None
    weather_data: Optional[Dict[str, WeatherData]] = None  


# Хранилище данных 
cities_db: Dict[str, City] = {}

# HTTP клиент для внешних запросов
http_client = httpx.AsyncClient(timeout=30.0)  


async def fetch_weather_from_api_at_moment(latitude: float, longitude: float) -> Dict:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,relative_humidity_2m,pressure_msl,wind_speed_10m,precipitation",
        "timezone": "auto"
    }
    
    try:
        response = await http_client.get(OPEN_METEO_URL, params=params)
        response.raise_for_status()
        data = response.json()
        current = data.get("current", {})
        return {
            "temperature": current.get("temperature_2m"),
            "humidity": current.get("relative_humidity_2m"),
            "pressure": current.get("pressure_msl"),
            "wind_speed": current.get("wind_speed_10m"),
            "precipitation": current.get("precipitation")
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Ошибка: {str(e)}")


async def fetch_daily_weather_from_api(latitude: float, longitude: float) -> Dict[str, WeatherData]:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "temperature_2m,relative_humidity_2m,pressure_msl,wind_speed_10m,precipitation",
        "timezone": "auto",
        "forecast_days": 1
    }

    try:
        response = await http_client.get(OPEN_METEO_URL, params=params)
        response.raise_for_status()
        data = response.json()
        
        hourly_data = data.get("hourly", {})
        times = hourly_data.get("time", [])
        temperatures = hourly_data.get("temperature_2m", [])
        pressures = hourly_data.get("pressure_msl", [])
        wind_speeds = hourly_data.get("wind_speed_10m", [])
        humidity = hourly_data.get("relative_humidity_2m", [])
        precipitation = hourly_data.get("precipitation", [])
        
        weather_by_hour = {}
        for i, time_str in enumerate(times):
            # "14:00"
            hour = time_str.split("T")[1][:5]
            
            weather_by_hour[hour] = WeatherData(
                temperature=temperatures[i] ,
                pressure=pressures[i] ,
                wind_speed=wind_speeds[i] ,
                humidity=humidity[i] ,
                precipitation=precipitation[i] 
            )
        
        return weather_by_hour
        
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Ошибка {str(e)}")


async def update_city_weather(city_name: str):
    if city_name not in cities_db:
        return
    
    city = cities_db[city_name]
    try:
        weather_data = await fetch_daily_weather_from_api(city.latitude, city.longitude)
        city.weather_data = weather_data
        city.last_update = datetime.now()
    except Exception as e:
        print(f"Ошибка {city_name}: {str(e)}")


async def periodic_weather_update():
    while True:
        try:
            if cities_db:
                tasks = [update_city_weather(city_name) for city_name in cities_db.keys()]
                await asyncio.gather(*tasks, return_exceptions=True)  
            await asyncio.sleep(UPDATE_INTERVAL)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Ошибка {str(e)}")
            await asyncio.sleep(UPDATE_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Запуск при старте
    update_task = asyncio.create_task(periodic_weather_update())

    yield
    
    update_task.cancel()
    try:
        await update_task
    except asyncio.CancelledError:
        pass
    
    await http_client.aclose()
    print("Weather API Service остановлен")


app = FastAPI(
    title="Weather API Service",
    description="REST API для получения информации о погоде через Open-Meteo",
    version="1.0.0",
    lifespan=lifespan
)


# Метод 1: Получение текущей погоды по координатам
@app.get("/weather/current")
async def get_current_weather(
    latitude: float = Query(..., ge=-90, le=90, ),
    longitude: float = Query(..., ge=-180, le=180)
):
    try:
        weather_data = await fetch_weather_from_api_at_moment(latitude, longitude)
        return {
            "latitude": latitude,
            "longitude": longitude,
            "weather": weather_data,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Ошибка получения погоды: {str(e)}")


# Метод 2: Добавление города для отслеживания
@app.get("/cities/add")  
async def add_city(
    name: str = Query(..., description="Название города"),
    latitude: float = Query(..., ge=-90, le=90, description="Широта"),
    longitude: float = Query(..., ge=-180, le=180, description="Долгота")
):
    if name in cities_db:
        raise HTTPException(status_code=400, detail=f"Город '{name}' уже существует")
    
    new_city = City(
        name=name,
        latitude=latitude,
        longitude=longitude,
        weather_data={}  
    )
    
    try:
        weather_data = await fetch_daily_weather_from_api(latitude, longitude)
        new_city.weather_data = weather_data
        new_city.last_update = datetime.now()
        weather_initialized = True
    except Exception as e:
        print(f"Ошибка получения данных для {name}: {str(e)}")
        weather_initialized = False
    
    cities_db[name] = new_city
    
    return {
        "message": f"Город '{name}' успешно добавлен",
        "city": {
            "name": name,
            "latitude": latitude,
            "longitude": longitude,
            "weather_initialized": weather_initialized
        }
    }

# Метод 3: Получение списка городов
@app.get("/cities")
async def get_cities() -> List[str]:
    return list(cities_db.keys())


# Метод 4: Получение погоды для города на указанное время
@app.get("/cities/{city_name}/weather")
async def get_city_weather_at_time(
    city_name: str,
    time: str = Query(),
    parameters: str = Query("temperature,wind_speed,pressure,humidity")
):
    if city_name not in cities_db:
        raise HTTPException(status_code=404, detail=f"Город '{city_name}' не найден")
    
    city = cities_db[city_name]
    
    if not city.weather_data or not city.last_update:
        await update_city_weather(city_name)
        if not city.weather_data:
            raise HTTPException(status_code=503, detail="Данные о погоде недоступны")
    
    hour_key = time[:2] + ":00"   
    weather_at_time = city.weather_data[hour_key]
    
    requested_params = [p.strip() for p in parameters.split(",")]
    valid_params = {"temperature", "wind_speed", "pressure", "humidity", "precipitation"}
    
    for param in requested_params:
        if param not in valid_params:
            raise HTTPException(
                status_code=400, 
                detail=f"Неверный параметр '{param}'. Допустимые параметры: {', '.join(valid_params)}"
            )
    response = {
        "city": city_name,
        "requested_time": time,
        "actual_time": hour_key,
        "weather": {},
        "last_update": city.last_update.isoformat() 
    }
    
    for param in requested_params:
        value = getattr(weather_at_time, param, None)
        if value is not None:
            response["weather"][param] = value
    
    return response

@app.get("/test")
async def run_http_tests():
    base_url = "http://127.0.0.1:8000"
    test_results = {
        "success": True,
        "steps": [],
        "errors": []
    }

    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        
        # Шаг 1: Текущая погода
        try:
            response = await client.get("/weather/current", params={
                "latitude": 55.7558,
                "longitude": 37.617
            })
            response.raise_for_status()
            
            test_results["steps"].append({
                "step": 1,
                "method": "GET",
                "endpoint": "/weather/current",
                "status_code": response.status_code,
                "result": response.json()
            })
        except Exception as e:
            test_results["success"] = False
            test_results["errors"].append({
                "step": 1,
                "error": str(e)
            })
        
        # Шаг 2: Список городов (до добавления)
        try:
            response = await client.get("/cities")
            response.raise_for_status()
            
            test_results["steps"].append({
                "step": 2,
                "method": "GET",
                "endpoint": "/cities",
                "status_code": response.status_code,
                "cities": response.json()
            })
        except Exception as e:
            test_results["errors"].append({
                "step": 2,
                "error": str(e)
            })
        
        # Шаг 3: Добавление Санкт-Петербурга
        try:
            response = await client.get("/cities/add", params={
                "name": "Saint-Petersburg",
                "latitude": 59.939,
                "longitude": 30.316
            })
            response.raise_for_status()
            
            test_results["steps"].append({
                "step": 3,
                "method": "POST",
                "endpoint": "/cities/add",
                "params": {"name": "Saint-Petersburg", "latitude": 59.939, "longitude": 30.316},
                "status_code": response.status_code,
                "result": response.json()
            })
        except Exception as e:
            test_results["success"] = False
            test_results["errors"].append({
                "step": 3,
                "error": str(e)
            })
        
        # Шаг 4: Добавление Москвы
        try:
            response = await client.get("/cities/add", params={
                "name": "Moscwa",
                "latitude": 55.7558,
                "longitude": 37.617
            })
            response.raise_for_status()
            
            test_results["steps"].append({
                "step": 4,
                "method": "POST",
                "endpoint": "/cities/add",
                "params": {"name": "Moscwa", "latitude": 55.7558, "longitude": 37.617},
                "status_code": response.status_code,
                "result": response.json()
            })
        except Exception as e:
            test_results["success"] = False
            test_results["errors"].append({
                "step": 4,
                "error": str(e)
            })
        
        # Шаг 5: Список городов (после добавления)
        try:
            response = await client.get("/cities")
            response.raise_for_status()
            
            test_results["steps"].append({
                "step": 5,
                "method": "GET",
                "endpoint": "/cities",
                "status_code": response.status_code,
                "cities": response.json()
            })
        except Exception as e:
            test_results["errors"].append({
                "step": 5,
                "error": str(e)
            })
        
        # Шаг 6: Погода для Москвы на 14:00
        try:
            response = await client.get("/cities/Moscwa/weather", params={
                "time": "14:00",
                "parameters": "temperature,pressure"
            })
            response.raise_for_status()
            
            test_results["steps"].append({
                "step": 6,
                "method": "GET",
                "endpoint": "/cities/Moscwa/weather",
                "params": {"time": "14:00", "parameters": "temperature,pressure"},
                "status_code": response.status_code,
                "result": response.json()
            })
        except Exception as e:
            test_results["success"] = False
            test_results["errors"].append({
                "step": 6,
                "error": str(e)
            })
    
    # Сводка
    test_results["summary"] = {
        "total_steps": 6,
        "successful": len(test_results["steps"]),
        "failed": len(test_results["errors"]),
        "timestamp": datetime.now().isoformat()
    }
        
    return test_results

if __name__ == "__main__":
    import uvicorn
    print("Запуск на http://127.0.0.1:8000")
    print("Документация  http://127.0.0.1:8000/docs")
    uvicorn.run(app, host="127.0.0.1", port=8000)