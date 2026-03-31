"""REINFORCE with baseline — replaces agent/4.policy-gradient-agent.ipynb.

Key differences from original:
  - Adds learned value baseline (original had no baseline = high variance)
  - Standard policy gradient loss: -log_prob(a) * (G - V(s))
    (original used a nonstandard `tf.log(input_y * (input_y - self.logits) + ...)`)
  - Entropy bonus for exploration (prevents premature convergence)
  - PyTorch autograd instead of manual TF1 computation
  - Interacts with TradingEnvironment (costs + risk)
"""

from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.distributions import Categorical

from openbb_forecast.agents.base import BaseAgent
from openbb_forecast.agents.environment import TradingEnvironment


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


class PolicyGradientAgent(BaseAgent):
    """REINFORCE with learned baseline and entropy regularization.

    Loss = -E[log pi(a|s) * (G_t - V(s))] - beta * H(pi)

    where G_t is the discounted return, V(s) is the learned baseline,
    and H(pi) is the entropy bonus.

    Args:
        state_size: State vector dimension.
        action_size: Number of actions (3: hold, buy, sell).
        hidden_size: Hidden layer size.
        learning_rate: Optimizer learning rate.
        gamma: Discount factor.
        entropy_beta: Entropy regularization weight.
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
        device: str = "auto",
    ):
        self.state_size = state_size
        self.action_size = action_size
        self.hidden_size = hidden_size
        self.learning_rate = learning_rate
        self.gamma = gamma
        self.entropy_beta = entropy_beta

        if device == "auto":
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = torch.device(device)

        self._build_networks()

    def _build_networks(self) -> None:
        self.policy_net = PolicyNetwork(self.state_size, self.action_size, self.hidden_size).to(
            self.device
        )
        self.value_net = ValueNetwork(self.state_size, self.hidden_size).to(self.device)
        self.policy_optimizer = torch.optim.Adam(self.policy_net.parameters(), lr=self.learning_rate)
        self.value_optimizer = torch.optim.Adam(self.value_net.parameters(), lr=self.learning_rate)

    def reset(self) -> None:
        self._build_networks()

    def select_action(self, state: np.ndarray, explore: bool = True) -> int:
        state_t = torch.FloatTensor(state).unsqueeze(0).to(self.device)
        with torch.no_grad():
            probs = self.policy_net(state_t)

        if explore:
            dist = Categorical(probs)
            return dist.sample().item()
        return int(probs.argmax(dim=1).item())

    def _compute_returns(self, rewards: list[float]) -> torch.Tensor:
        """Compute discounted returns G_t = sum_{k=0}^{T-t} gamma^k * r_{t+k}."""
        returns = []
        G = 0.0
        for r in reversed(rewards):
            G = r + self.gamma * G
            returns.insert(0, G)
        returns_t = torch.FloatTensor(returns).to(self.device)
        # Normalize for stability
        if len(returns_t) > 1:
            returns_t = (returns_t - returns_t.mean()) / (returns_t.std() + 1e-8)
        return returns_t

    def train(self, env: TradingEnvironment, episodes: int) -> list[float]:
        """Train using REINFORCE with baseline.

        Each episode:
          1. Collect full trajectory (states, actions, rewards)
          2. Compute discounted returns
          3. Update value network (baseline) to minimize MSE with returns
          4. Update policy using advantage (return - baseline)
          5. Add entropy bonus to prevent collapse
        """
        episode_rewards = []

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

            episode_rewards.append(sum(rewards))

            if not rewards:
                continue

            # Compute returns
            returns = self._compute_returns(rewards)

            # Convert to tensors
            states_t = torch.FloatTensor(np.array(states)).to(self.device)
            actions_t = torch.LongTensor(actions).to(self.device)

            # Value network update
            values = self.value_net(states_t).squeeze()
            value_loss = F.mse_loss(values, returns)
            self.value_optimizer.zero_grad()
            value_loss.backward()
            torch.nn.utils.clip_grad_norm_(self.value_net.parameters(), max_norm=1.0)
            self.value_optimizer.step()

            # Policy network update
            probs = self.policy_net(states_t)
            dist = Categorical(probs)
            log_probs = dist.log_prob(actions_t)

            with torch.no_grad():
                baselines = self.value_net(states_t).squeeze()

            advantages = returns - baselines
            policy_loss = -(log_probs * advantages).mean()

            # Entropy bonus
            entropy = dist.entropy().mean()
            total_loss = policy_loss - self.entropy_beta * entropy

            self.policy_optimizer.zero_grad()
            total_loss.backward()
            torch.nn.utils.clip_grad_norm_(self.policy_net.parameters(), max_norm=1.0)
            self.policy_optimizer.step()

        return episode_rewards
