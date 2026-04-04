"""
llm_management.py — Dynamic LLM Registration API
"""
from fastapi import APIRouter, HTTPException, Request
from typing import List, Dict, Any
from config import LLMConfig
from llm_manager import llm_manager
from llm_status_store import llm_status_store
import json

router = APIRouter(prefix="/llms", tags=["llm-management"])

@router.get("")
async def list_llms():
    """List all registered LLMs with their current status and metrics."""
    llms = llm_manager.all_llms()
    stats = await llm_status_store.get_all_stats()
    
    result = []
    for llm in llms:
        status = llm_manager.get_status(llm.name)
        llm_stats = stats.get(llm.name, {})
        
        info = llm.model_dump()
        info["status"] = status.model_dump() if status else None
        info["metrics"] = llm_stats
        
        result.append(info)
        
    return result

@router.post("")
async def register_llm(llm: LLMConfig):
    """Register a new LLM dynamically."""
    if llm_manager.get_llm(llm.name):
        raise HTTPException(status_code=400, detail=f"LLM with name '{llm.name}' already exists.")
    
    from llm_client.adapters import get_adapter
    adapter = get_adapter(llm)
    healthy = await adapter.health_check()
    
    llm_manager.register_llm(llm)
    return {"status": "registered", "name": llm.name, "healthy_on_registration": healthy}

@router.put("/{name}")
async def update_llm(name: str, config_update: dict):
    """Update an existing LLM configuration."""
    existing = llm_manager.get_llm(name)
    if not existing:
        raise HTTPException(status_code=404, detail=f"LLM '{name}' not found.")
        
    # Update fields
    updated_dict = existing.model_dump()
    for k, v in config_update.items():
        if k in updated_dict:
            updated_dict[k] = v
            
    updated_llm = LLMConfig(**updated_dict)
    llm_manager.register_llm(updated_llm) # This will overwrite the existing one
    return {"status": "updated", "name": name}

@router.delete("/{name}")
async def remove_llm(name: str):
    """Remove an LLM from the active rotation."""
    existing = llm_manager.get_llm(name)
    if not existing:
        raise HTTPException(status_code=404, detail=f"LLM '{name}' not found.")
        
    if existing.role == "primary" and len(llm_manager.healthy_llms()) <= 1:
        raise HTTPException(status_code=400, detail="Cannot remove the only healthy primary LLM.")
        
    # Remove from manager dictionary
    if name in llm_manager.llms:
        del llm_manager.llms[name]
    if name in llm_manager.status:
        del llm_manager.status[name]
        
    return {"status": "removed", "name": name}

@router.post("/{name}/test")
async def test_llm(name: str, request: Request):
    """Run a live connectivity test using its adapter."""
    existing = llm_manager.get_llm(name)
    if not existing:
        raise HTTPException(status_code=404, detail=f"LLM '{name}' not found.")
        
    body = await request.json()
    prompt = body.get("prompt", "Hello, are you receiving my prompt?")
    
    from llm_client.adapters import get_adapter
    adapter = get_adapter(existing)
    
    try:
        response = await adapter.generate([{"role": "user", "content": prompt}])
        return {"status": "success", "response": response}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@router.post("/{name}/toggle")
async def toggle_llm(name: str, enabled: bool):
    """Enable or disable an LLM without removing it."""
    existing = llm_manager.get_llm(name)
    if not existing:
        raise HTTPException(status_code=404, detail=f"LLM '{name}' not found.")
        
    existing.enabled = enabled
    llm_manager.register_llm(existing)
    return {"status": "toggled", "name": name, "enabled": enabled}

@router.get("/{name}/stats")
async def llm_stats(name: str):
    """Get detailed metrics for a specific LLM."""
    stats = await llm_status_store.get_llm_stats(name)
    if not stats:
        raise HTTPException(status_code=404, detail=f"No stats found for '{name}'.")
    return stats

@router.get("/{name}/history")
async def llm_history(name: str, limit: int = 100):
    """Get latency and availability history."""
    return await llm_status_store.get_llm_history(name, limit)
