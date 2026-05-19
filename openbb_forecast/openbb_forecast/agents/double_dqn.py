"""Double DQN agent — with early stopping, best checkpoint, Sharpe monitoring.

Key features:
  - Double Q-learning: action from online network, value from target network
  - Experience replay with proper sampling
  - Epsilon decay per episode (multiplicative)
  - Gradient clipping for stability (max_norm=1.0)
  - Early stopping with best checkpoint (not last epoch)
  - SharpeMonitor: auto-stop on persistent negative Sharpe
  - TradingEnvironment integration (costs + risk)
"""

from __future__ import annotations

import logging
import os
import random
from collections import deque
from datetime import datetime
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from openbb_forecast.agents.base import BaseAgent
from openbb_forecast.agents.environment import TradingEnvironment

logger = logging.getLogger("forecast.dqn")


class QNetwork(nn.Module):
    """Q-value network: state -> Q(s, a) for each action."""

    def __init__(self, state_size: int, action_size: int, hidden_size: int = 256):
        super().__init__()
        self.fc1 = nn.Linear(state_size, hidden_size)
        self.fc2 = nn.Linear(hidden_size, hidden_size)
        self.fc3 = nn.Linear(hidden_size, action_size)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        return self.fc3(x)


class EarlyStopping:
    """Track best metric; stop training when no improvement for `patience` evals."""

    def __init__(self, patience: int = 10, min_delta: float = 0.001, mode: str = "max"):
        self.patience = patience
        self.min_delta = min_delta
        self.mode = mode
        self.best_score = None
        self.best_epoch = 0
        self.counter = 0
        self.early_stop = False

    def step(self, score: float, epoch: int) -> bool:
        """Return True if this is a new best score."""
        is_better = False
        if self.best_score is None:
            is_better = True
        elif self.mode == "max" and score > self.best_score + self.min_delta:
            is_better = True
        elif self.mode == "min" and score < self.best_score - self.min_delta:
            is_better = True

        if is_better:
            self.best_score = score
            self.best_epoch = epoch
            self.counter = 0
            return True
        else:
            self.counter += 1
            if self.counter >= self.patience:
                self.early_stop = True
            return False


class SharpeMonitor:
    """Monitor rolling Sharpe; raise flag on persistent negatives."""

    def __init__(self, window: int = 10, max_consecutive: int = 5):
        self.window = window
        self.max_consecutive = max_consecutive
        self.rewards_history: list[float] = []
        self.consecutive_negative = 0

    def update(self, episode_rewards: list[float]) -> bool:
        """Return True if training should STOP."""
        self.rewards_history.extend(episode_rewards)
        if len(self.rewards_history) < self.window:
            return False
        recent = self.rewards_history[-self.window:]
        if len(recent) < 2:
            return False
        avg_r = np.mean(recent)
        std_r = np.std(recent) + 1e-9
        sharpe = (avg_r / std_r) * np.sqrt(252)

        if sharpe < 0:
            self.consecutive_negative += 1
        else:
            self.consecutive_negative = 0

        if self.consecutive_negative >= self.max_consecutive:
            logger.warning("SharpeMonitor: %d consecutive negative-Sharpe windows → stopping",
                           self.consecutive_negative)
            return True
        return False


class DoubleDQNAgent(BaseAgent):
    """Double DQN trading agent with early stopping, best checkpoint, and gradient clipping.

    Args:
        state_size: Dimension of state vector.
        action_size: Number of actions (3: hold, buy, sell).
        hidden_size: Hidden layer size.
        learning_rate: Adam learning rate.
        gamma: Discount factor.
        epsilon_start: Initial exploration rate.
        epsilon_end: Minimum exploration rate.
        epsilon_decay: Multiplicative decay per episode.
        batch_size: Replay sample size.
        memory_size: Replay buffer capacity.
        target_update: Steps between target network syncs.
        grad_clip: Max gradient norm (default 1.0).
        early_stop_patience: Episodes without improvement before stopping.
        early_stop_min_delta: Minimum improvement threshold.
        checkpoint_dir: Directory for best model checkpoint.
        checkpoint_name: Base filename for checkpoint.
        device: 'cuda', 'cpu', or 'auto'.
    """

    def __init__(
        self,
        state_size: int,
        action_size: int = 3,
        hidden_size: int = 256,
        learning_rate: float = 0.001,
        gamma: float = 0.99,
        epsilon_start: float = 1.0,
        epsilon_end: float = 0.01,
        epsilon_decay: float = 0.995,
        batch_size: int = 64,
        memory_size: int = 10_000,
        target_update: int = 1000,
        grad_clip: float = 1.0,
        early_stop_patience: int = 15,
        early_stop_min_delta: float = 0.001,
        checkpoint_dir: str = "model_checkpoints",
        checkpoint_name: str = "double_dqn",
        device: str = "auto",
    ):
        self.state_size = state_size
        self.action_size = action_size
        self.hidden_size = hidden_size
        self.learning_rate = learning_rate
        self.gamma = gamma
        self.epsilon = epsilon_start
        self.epsilon_start = epsilon_start
        self.epsilon_end = epsilon_end
        self.epsilon_decay = epsilon_decay
        self.batch_size = batch_size
        self.memory_size = memory_size
        self.target_update = target_update
        self.grad_clip = grad_clip

        if device == "auto":
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = torch.device(device)

        self._build_networks()
        self.memory: deque = deque(maxlen=memory_size)
        self._step_count = 0

        # Early stopping + checkpoint
        self.early_stopping = EarlyStopping(
            patience=early_stop_patience,
            min_delta=early_stop_min_delta,
            mode="max",
        )
        self.sharpe_monitor = SharpeMonitor()
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_name = checkpoint_name
        self.best_checkpoint_path: str | None = None

    def _build_networks(self) -> None:
        self.online_net = QNetwork(
            self.state_size, self.action_size, self.hidden_size
        ).to(self.device)
        self.target_net = QNetwork(
            self.state_size, self.action_size, self.hidden_size
        ).to(self.device)
        self.target_net.load_state_dict(self.online_net.state_dict())
        self.target_net.eval()
        self.optimizer = torch.optim.Adam(
            self.online_net.parameters(), lr=self.learning_rate
        )

    def reset(self) -> None:
        self._build_networks()
        self.memory = deque(maxlen=self.memory_size)
        self.epsilon = self.epsilon_start
        self._step_count = 0
        self.early_stopping = EarlyStopping(
            patience=self.early_stopping.patience,
            min_delta=self.early_stopping.min_delta,
            mode="max",
        )
        self.sharpe_monitor = SharpeMonitor()
        self.best_checkpoint_path = None

    def select_action(self, state: np.ndarray, explore: bool = True) -> int:
        if explore and random.random() < self.epsilon:
            return random.randrange(self.action_size)

        state_t = torch.FloatTensor(state).unsqueeze(0).to(self.device)
        with torch.no_grad():
            q_values = self.online_net(state_t)
        return int(q_values.argmax(dim=1).item())

    def _remember(self, state, action, reward, next_state, done):
        self.memory.append((state, action, reward, next_state, done))

    def _replay(self) -> float:
        """Sample replay buffer; train one step. Returns loss."""
        if len(self.memory) < self.batch_size:
            return 0.0

        batch = random.sample(self.memory, self.batch_size)
        states, actions, rewards, next_states, dones = zip(*batch)

        states_t = torch.FloatTensor(np.array(states)).to(self.device)
        actions_t = torch.LongTensor(actions).unsqueeze(1).to(self.device)
        rewards_t = torch.FloatTensor(rewards).unsqueeze(1).to(self.device)
        next_states_t = torch.FloatTensor(np.array(next_states)).to(self.device)
        dones_t = torch.BoolTensor(dones).unsqueeze(1).to(self.device)

        current_q = self.online_net(states_t).gather(1, actions_t)

        with torch.no_grad():
            next_actions = self.online_net(next_states_t).argmax(dim=1, keepdim=True)
            next_q = self.target_net(next_states_t).gather(1, next_actions)
            next_q[dones_t] = 0.0
            target_q = rewards_t + self.gamma * next_q

        loss = F.mse_loss(current_q, target_q)
        self.optimizer.zero_grad()
        loss.backward()
        # Gradient clipping
        torch.nn.utils.clip_grad_norm_(self.online_net.parameters(), max_norm=self.grad_clip)
        self.optimizer.step()

        self._step_count += 1
        if self._step_count % self.target_update == 0:
            self.target_net.load_state_dict(self.online_net.state_dict())

        return loss.item()

    def _save_checkpoint(self, episode: int, reward: float) -> str:
        """Save model state dict as best checkpoint."""
        ckpt = {
            "episode": episode,
            "reward": reward,
            "epsilon": self.epsilon,
            "online_state": self.online_net.state_dict(),
            "target_state": self.target_net.state_dict(),
            "optimizer_state": self.optimizer.state_dict(),
        }
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.checkpoint_dir / f"{self.checkpoint_name}_best_ep{episode}_{ts}.pt"
        # Also save as the canonical 'best' path
        best_path = self.checkpoint_dir / f"{self.checkpoint_name}_best.pt"
        torch.save(ckpt, str(path))
        torch.save(ckpt, str(best_path))
        self.best_checkpoint_path = str(best_path)

        # Cleanup: keep only the 3 most recent checkpoint files
        files = sorted(
            self.checkpoint_dir.glob(f"{self.checkpoint_name}_best_ep*.pt"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for f in files[3:]:
            try:
                f.unlink()
            except Exception:
                pass

        return str(path)

    def load_best(self) -> bool:
        """Load the best checkpoint if it exists. Returns True on success."""
        best_path = self.checkpoint_dir / f"{self.checkpoint_name}_best.pt"
        if not best_path.exists():
            logger.warning("No best checkpoint found at %s", best_path)
            return False
        try:
            ckpt = torch.load(str(best_path), map_location=self.device, weights_only=False)
            self.online_net.load_state_dict(ckpt["online_state"])
            self.target_net.load_state_dict(ckpt["target_state"])
            self.optimizer.load_state_dict(ckpt["optimizer_state"])
            self.epsilon = ckpt.get("epsilon", self.epsilon_end)
            logger.info("Loaded best checkpoint from episode %d (reward=%.4f)",
                        ckpt.get("episode", 0), ckpt.get("reward", 0))
            return True
        except Exception as e:
            logger.error("Failed to load best checkpoint: %s", e)
            return False

    def train(
        self,
        env: TradingEnvironment,
        episodes: int,
        eval_interval: int = 5,
        early_stop_metric: str = "reward",
        verbose: bool = False,
    ) -> dict:
        """Train agent with early stopping and best-checkpoint tracking.

        Returns dict with:
            episode_rewards, best_epoch, best_reward, early_stopped,
            sharpe_stopped, checkpoint_path, training_history.
        """
        episode_rewards: list[float] = []
        training_history: list[dict] = []
        best_reward = float("-inf")
        best_epoch = 0
        early_stopped = False
        sharpe_stopped = False

        for ep in range(episodes):
            state = env.reset()
            total_reward = 0.0
            step_count = 0

            while True:
                action = self.select_action(state, explore=True)
                next_state, reward, done, info = env.step(action)
                self._remember(state, action, reward, next_state, done)
                self._replay()
                total_reward += reward
                state = next_state
                step_count += 1

                if done:
                    break

            # Decay epsilon
            self.epsilon = max(self.epsilon_end, self.epsilon * self.epsilon_decay)
            episode_rewards.append(total_reward)

            # Sharpe monitor check
            if self.sharpe_monitor.update([total_reward]):
                if verbose:
                    logger.warning("SharpeMonitor triggered at episode %d", ep + 1)
                sharpe_stopped = True
                break

            # Early stopping check (every eval_interval)
            if (ep + 1) % eval_interval == 0 or ep == episodes - 1:
                # Use recent average reward as proxy metric
                recent_window = min(10, len(episode_rewards))
                avg_recent = np.mean(episode_rewards[-recent_window:])

                is_best = self.early_stopping.step(avg_recent, ep + 1)

                if is_best:
                    ckpt_path = self._save_checkpoint(ep + 1, avg_recent)
                    best_reward = avg_recent
                    best_epoch = ep + 1
                    if verbose:
                        logger.info("Ep %d/%d: best reward=%.4f → checkpoint saved",
                                    ep + 1, episodes, avg_recent)

                training_history.append({
                    "episode": ep + 1,
                    "reward": round(total_reward, 4),
                    "avg_recent": round(float(avg_recent), 4),
                    "epsilon": round(self.epsilon, 4),
                    "is_best": is_best,
                })

                if self.early_stopping.early_stop:
                    if verbose:
                        logger.info("Early stopping at episode %d (best=%d)",
                                    ep + 1, best_epoch)
                    early_stopped = True
                    break

        # Load best checkpoint on completion
        if self.best_checkpoint_path:
            self.load_best()

        return {
            "episode_rewards": episode_rewards,
            "best_epoch": best_epoch,
            "best_reward": round(float(best_reward), 4),
            "early_stopped": early_stopped,
            "sharpe_stopped": sharpe_stopped,
            "checkpoint_path": self.best_checkpoint_path,
            "training_history": training_history,
            "total_episodes_run": len(episode_rewards),
        }
