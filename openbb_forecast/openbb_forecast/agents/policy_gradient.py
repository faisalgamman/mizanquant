"""REINFORCE with baseline — early stopping, best checkpoint, gradient clipping.

Key features:
  - Learned value baseline (reduces variance vs raw REINFORCE)
  - Standard policy gradient loss: -log_prob(a) × (G - V(s))
  - Entropy bonus for exploration
  - Gradient clipping (max_norm=1.0) for both policy and value networks
  - Early stopping with best checkpoint (not last epoch)
  - SharpeMonitor: auto-stop on persistent negative Sharpe
  - Interacts with TradingEnvironment (costs + risk)
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.distributions import Categorical

from openbb_forecast.agents.base import BaseAgent
from openbb_forecast.agents.environment import TradingEnvironment

logger = logging.getLogger("forecast.pg")


class PolicyNetwork(nn.Module):
    """Policy: state -> action probabilities."""

    def __init__(self, state_size: int, action_size: int, hidden_size: int = 256):
        super().__init__()
        self.fc1 = nn.Linear(state_size, hidden_size)
        self.fc2 = nn.Linear(hidden_size, hidden_size)
        self.fc3 = nn.Linear(hidden_size, action_size)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        return F.softmax(self.fc3(x), dim=-1)


class ValueNetwork(nn.Module):
    """Baseline value function: state -> scalar value estimate."""

    def __init__(self, state_size: int, hidden_size: int = 256):
        super().__init__()
        self.fc1 = nn.Linear(state_size, hidden_size)
        self.fc2 = nn.Linear(hidden_size, hidden_size)
        self.fc3 = nn.Linear(hidden_size, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        return self.fc3(x)


# Shared utility classes (same as double_dqn.py)
class EarlyStopping:
    def __init__(self, patience: int = 10, min_delta: float = 0.001, mode: str = "max"):
        self.patience = patience
        self.min_delta = min_delta
        self.mode = mode
        self.best_score = None
        self.best_epoch = 0
        self.counter = 0
        self.early_stop = False

    def step(self, score: float, epoch: int) -> bool:
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
    def __init__(self, window: int = 10, max_consecutive: int = 5):
        self.window = window
        self.max_consecutive = max_consecutive
        self.rewards_history: list[float] = []
        self.consecutive_negative = 0

    def update(self, episode_rewards: list[float]) -> bool:
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


class PolicyGradientAgent(BaseAgent):
    """REINFORCE with baseline, entropy regularization, early stopping, best checkpoint.

    Loss = -E[log pi(a|s) * (G_t - V(s))] - beta * H(pi)

    Args:
        state_size: State vector dimension.
        action_size: Number of actions (3: hold, buy, sell).
        hidden_size: Hidden layer size.
        learning_rate: Optimizer learning rate.
        gamma: Discount factor.
        entropy_beta: Entropy regularization weight.
        grad_clip: Max gradient norm (default 1.0).
        early_stop_patience: Episodes without improvement before stopping.
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
        entropy_beta: float = 0.01,
        grad_clip: float = 1.0,
        early_stop_patience: int = 15,
        early_stop_min_delta: float = 0.001,
        checkpoint_dir: str = "model_checkpoints",
        checkpoint_name: str = "policy_gradient",
        device: str = "auto",
    ):
        self.state_size = state_size
        self.action_size = action_size
        self.hidden_size = hidden_size
        self.learning_rate = learning_rate
        self.gamma = gamma
        self.entropy_beta = entropy_beta
        self.grad_clip = grad_clip

        if device == "auto":
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = torch.device(device)

        self._build_networks()

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
        self.policy_net = PolicyNetwork(
            self.state_size, self.action_size, self.hidden_size
        ).to(self.device)
        self.value_net = ValueNetwork(self.state_size, self.hidden_size).to(self.device)
        self.policy_optimizer = torch.optim.Adam(
            self.policy_net.parameters(), lr=self.learning_rate
        )
        self.value_optimizer = torch.optim.Adam(
            self.value_net.parameters(), lr=self.learning_rate
        )

    def reset(self) -> None:
        self._build_networks()
        self.early_stopping = EarlyStopping(
            patience=self.early_stopping.patience,
            min_delta=self.early_stopping.min_delta,
            mode="max",
        )
        self.sharpe_monitor = SharpeMonitor()
        self.best_checkpoint_path = None

    def select_action(self, state: np.ndarray, explore: bool = True) -> int:
        state_t = torch.FloatTensor(state).unsqueeze(0).to(self.device)
        with torch.no_grad():
            probs = self.policy_net(state_t)
        if explore:
            dist = Categorical(probs)
            return dist.sample().item()
        return int(probs.argmax(dim=1).item())

    def _compute_returns(self, rewards: list[float]) -> torch.Tensor:
        returns = []
        G = 0.0
        for r in reversed(rewards):
            G = r + self.gamma * G
            returns.insert(0, G)
        returns_t = torch.FloatTensor(returns).to(self.device)
        if len(returns_t) > 1:
            returns_t = (returns_t - returns_t.mean()) / (returns_t.std() + 1e-8)
        return returns_t

    def _save_checkpoint(self, episode: int, reward: float) -> str:
        ckpt = {
            "episode": episode,
            "reward": reward,
            "policy_state": self.policy_net.state_dict(),
            "value_state": self.value_net.state_dict(),
            "policy_opt_state": self.policy_optimizer.state_dict(),
            "value_opt_state": self.value_optimizer.state_dict(),
        }
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.checkpoint_dir / f"{self.checkpoint_name}_best_ep{episode}_{ts}.pt"
        best_path = self.checkpoint_dir / f"{self.checkpoint_name}_best.pt"
        torch.save(ckpt, str(path))
        torch.save(ckpt, str(best_path))
        self.best_checkpoint_path = str(best_path)

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
        best_path = self.checkpoint_dir / f"{self.checkpoint_name}_best.pt"
        if not best_path.exists():
            logger.warning("No best checkpoint found at %s", best_path)
            return False
        try:
            ckpt = torch.load(str(best_path), map_location=self.device, weights_only=False)
            self.policy_net.load_state_dict(ckpt["policy_state"])
            self.value_net.load_state_dict(ckpt["value_state"])
            self.policy_optimizer.load_state_dict(ckpt["policy_opt_state"])
            self.value_optimizer.load_state_dict(ckpt["value_opt_state"])
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
        verbose: bool = False,
    ) -> dict:
        """Train using REINFORCE with baseline, early stopping, best checkpoint.

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
            states, actions, rewards = [], [], []

            while True:
                state_t = torch.FloatTensor(state).unsqueeze(0).to(self.device)
                probs = self.policy_net(state_t)
                dist = Categorical(probs)
                action = dist.sample()

                next_state, reward, done, info = env.step(action.item())

                states.append(state)
                actions.append(action.item())
                rewards.append(reward)
                state = next_state

                if done:
                    break

            total_reward = sum(rewards)
            episode_rewards.append(total_reward)

            if not rewards:
                continue

            # Compute returns
            returns = self._compute_returns(rewards)
            states_t = torch.FloatTensor(np.array(states)).to(self.device)
            actions_t = torch.LongTensor(actions).to(self.device)

            # Value network update
            values = self.value_net(states_t).squeeze()
            value_loss = F.mse_loss(values, returns)
            self.value_optimizer.zero_grad()
            value_loss.backward()
            torch.nn.utils.clip_grad_norm_(self.value_net.parameters(), max_norm=self.grad_clip)
            self.value_optimizer.step()

            # Policy network update
            probs = self.policy_net(states_t)
            dist = Categorical(probs)
            log_probs = dist.log_prob(actions_t)

            with torch.no_grad():
                baselines = self.value_net(states_t).squeeze()

            advantages = returns - baselines
            policy_loss = -(log_probs * advantages).mean()
            entropy = dist.entropy().mean()
            total_loss = policy_loss - self.entropy_beta * entropy

            self.policy_optimizer.zero_grad()
            total_loss.backward()
            torch.nn.utils.clip_grad_norm_(self.policy_net.parameters(), max_norm=self.grad_clip)
            self.policy_optimizer.step()

            # SharpeMonitor
            if self.sharpe_monitor.update([total_reward]):
                sharpe_stopped = True
                if verbose:
                    logger.warning("SharpeMonitor triggered at episode %d", ep + 1)
                break

            # Early stopping check
            if (ep + 1) % eval_interval == 0 or ep == episodes - 1:
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
                    "is_best": is_best,
                })

                if self.early_stopping.early_stop:
                    early_stopped = True
                    if verbose:
                        logger.info("Early stopping at episode %d (best=%d)",
                                    ep + 1, best_epoch)
                    break

        # Load best checkpoint
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
