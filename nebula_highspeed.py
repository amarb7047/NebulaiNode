import hashlib, time, aiohttp, asyncio, numpy as np, os
from typing import Optional, Tuple
import concurrent.futures

def generate_matrix(seed: int, size: int) -> np.ndarray:
    matrix = np.empty((size, size), dtype=np.float64)
    current_seed = seed
    a, b = 0x4b72e682d, 0x2675dcd22
    for i in range(size):
        for j in range(size):
            value = (a * current_seed + b) % 1000
            matrix[i][j] = float(value)
            current_seed = value
    return matrix

def multiply_matrices(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return a @ b

def flatten_matrix(matrix: np.ndarray) -> str:
    return ''.join(f"{x:.0f}" for x in matrix.flat)

async def compute_hash_mod(matrix: np.ndarray, mod: int = 10**7) -> int:
    flat_str = flatten_matrix(matrix)
    sha256 = hashlib.sha256(flat_str.encode()).hexdigest()
    return int(int(sha256, 16) % mod)

async def fetch_task(session: aiohttp.ClientSession, token: str) -> Tuple[Optional[dict], bool, bool]:
    headers = {"Content-Type": "application/json", "token": token}
    try:
        async with session.post("https://nebulai.network/open_compute/finish/task", json={}, headers=headers, timeout=8) as resp:
            data = await resp.json()
            if data.get("error") == "jwt auth err":
                print(f"[⛔] JWT expired for {token[:8]}")
                with open("expired_tokens.txt", "a") as f:
                    f.write(token + "\n")
                return None, False, False
            if data.get("code") == -429:
                print(f"[⚠️] 429 Rate Limit for {token[:8]} (fetch)")
                return None, False, True
            if data.get("code") == 0:
                return data['data'], True, False
            return None, False, False
    except Exception as e:
        print(f"[❌] Fetch error {token[:8]}: {str(e)}")
        return None, False, False

async def submit_results(session: aiohttp.ClientSession, token: str, r1: float, r2: float, task_id: str) -> Tuple[bool, bool]:
    headers = {"Content-Type": "application/json", "token": token}
    payload = {"result_1": f"{r1:.10f}", "result_2": f"{r2:.10f}", "task_id": task_id}
    try:
        async with session.post("https://nebulai.network/open_compute/finish/task", json=payload, headers=headers, timeout=8) as resp:
            data = await resp.json()
            loops = data.get("data", {}).get("loops", 0)
            if data.get("code") == -429:
                print(f"[⚠️] 429 Rate Limit for {token[:8]} (submit)")
                return False, True
            if data.get("code") == 0 and data.get("data", {}).get("calc_status", False):
                print(f"[✅] Submit {token[:8]} OK (Loops: {loops})")
                return True, False
            print(f"[❌] Submit {token[:8]} Failed: {data}")
            return False, False
    except Exception as e:
        print(f"[❌] Submit error {token[:8]}: {str(e)}")
        return False, False

async def process_task(token: str, task_data: dict) -> Optional[Tuple[float, float, float]]:
    seed1, seed2, size = task_data["seed1"], task_data["seed2"], task_data["matrix_size"]
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
            t0 = time.time() * 1000
            A_future = executor.submit(generate_matrix, seed1, size)
            B_future = executor.submit(generate_matrix, seed2, size)
            A, B = await asyncio.gather(
                asyncio.wrap_future(A_future),
                asyncio.wrap_future(B_future)
            )
        C = multiply_matrices(A, B)
        f = await compute_hash_mod(C)
        t1 = time.time() * 1000
        r1 = t0 / f
        r2 = f / (t1 - t0) if (t1 - t0) != 0 else 0
        return r1, r2, (t1 - t0) / 1000
    except Exception as e:
        print(f"[❌] Compute error {token[:8]}: {str(e)}")
        return None

async def worker_loop(token: str):
    success = fail = total = 0
    total_time = 0.0
    start_time = time.time()
    async with aiohttp.ClientSession() as session:
        while True:
            task_data, ok, rate_limit = await fetch_task(session, token)
            if rate_limit:
                await asyncio.sleep(3)
                continue
            if not ok:
                break
            result = await process_task(token, task_data)
            if not result:
                fail += 1
                continue
            r1, r2, dur = result
            total += 1
            total_time += dur
            submitted, rate_limit = await submit_results(session, token, r1, r2, task_data["task_id"])
            if rate_limit:
                await asyncio.sleep(3)
                continue
            if submitted:
                success += 1
            else:
                fail += 1
            await asyncio.sleep(0.05)

    runtime = time.time() - start_time
    earned = success * 0.003
    print(f"📊 Token {token[:8]} ➜ ✅ {success}, ❌ {fail}, 🌀 {total}, ⏱️ {runtime:.1f}s, 🪙 {earned:.3f} pts")

async def main():
    if not os.path.exists("tokens.txt"):
        print("tokens.txt not found!")
        return
    with open("tokens.txt") as f:
        tokens = [line.strip() for line in f if line.strip()]
    if not tokens:
        print("No valid tokens in tokens.txt!")
        return
    await asyncio.gather(*(worker_loop(token) for token in tokens))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\\n[⛔] Stopped by user")
