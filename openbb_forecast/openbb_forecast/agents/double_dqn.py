"""Double DQN agent — replaces agent/7.double-q-learning-agent.ipynb.

Key differences from original:
  - PyTorch instead of TF1
  - Double Q-learning: action from online network, value from target network
    (reduces overestimation bias in Q-values)
  - Experience replay buffer with proper sampling
  - Epsilon decay per episode (multiplicative)
  - Interacts with TradingEnvironment (costs + risk), not raw prices
  - Gradient clipping for stability
"""

from __future__ import annotations

import random
from collections import deque

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from openbb_forecast.agents.base import BaseAgent
from openbb_forecast.agents.environment import TradingEnvironment


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


class DoubleDQNAgent(BaseAgent):
    """Double DQN trading agent with experience replay and target network.

    The Double DQN fix addresses Q-value overestimation:
      - Online network selects the best action: a* = argmax_a Q_online(s', a)
      - Target network evaluates it: y = r + gamma * Q_target(s', a*)

    Args:
        state_size: Dimension of the state vector.
        action_size: Number of possible actions (3: hold, buy, sell).
        hidden_size: Hidden layer size.
        learning_rate: Adam optimizer learning rate.
        gamma: Discount factor for future rewards.
        epsilon_start: Initial exploration rate.
        epsilon_end: Minimum exploration rate.
        epsilon_decay: Multiplicative decay per step.
        batch_size: Replay buffer sample size.
        memory_size: Maximum replay buffer capacity.
        target_update: Steps between target network syncs.
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

        if device == "auto":
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = torch.device(device)

        self._build_networks()
        self.memory: deque = deque(maxlen=memory_size)
        self._step_count = 0

    def _build_networks(self) -> None:
        self.online_net = QNetwork(self.state_size, self.action_size, self.hidden_size).to(self.device)
        self.target_net = QNetwork(self.state_size, self.action_size, self.hidden_size).to(self.device)
        self.target_net.load_state_dict(self.online_net.state_dict())
        self.target_net.eval()
        self.optimizer = torch.optim.Adam(self.online_net.parameters(), lr=self.learning_rate)

    def reset(self) -> None:
        self._build_networks()
        self.memory = deque(maxlen=self.memory_size)
        self.epsilon = self.epsilon_start
        self._step_count = 0

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
        """Sample from replay buffer and train one step. Returns loss."""
        if len(self.memory) < self.batch_size:
            return 0.0

        batch = random.sample(self.memory, self.batch_size)
        states, actions, rewards, next_states, dones = zip(*batch)

        states_t = torch.FloatTensor(np.array(states)).to(self.device)
        actions_t = torch.LongTensor(actions).unsqueeze(1).to(self.device)
        rewards_t = torch.FloatTensor(rewards).unsqueeze(1).to(self.device)
        next_states_t = torch.FloatTensor(np.array(next_states)).to(self.device)
        dones_t = torch.BoolTensor(dones).unsqueeze(1).to(self.device)

        # Current Q-values for chosen actions
        current_q = self.online_net(states_t).gather(1, actions_t)

        # Double DQN: online net selects action, target net evaluates
        with torch.no_grad():
            next_actions = self.online_net(next_states_t).argmax(dim=1, keepdim=True)
            next_q = self.target_net(next_states_t).gather(1, next_actions)
            next_q[dones_t] = 0.0
            target_q = rewards_t + self.gamma * next_q

        loss = F.mse_loss(current_q, target_q)
        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.online_net.parameters(), max_norm=1.0)
        self.optimizer.step()

        # Update target network periodically
        self._step_count += 1
        if self._step_count % self.target_update == 0:
            self.target_net.load_state_dict(self.online_net.state_dict())

        return loss.item()

    def train(self, env: TradingEnvironment, episodes: int) -> list[float]:
        """Train agent via experience replay.

        Returns list of total rewards per episode.
        """
        episode_rewards = []

        for ep in range(episodes):
            state = env.reset()
            total_reward = 0.0

            while True:
                action = self.select_action(state, explore=True)
                next_state, reward, done, info = env.step(action)
                self._remember(state, action, reward, next_state, done)
                self._replay()

                total_reward += reward
                state = next_state

                if done:
                    break

            # Decay epsilon
            self.epsilon = max(self.epsilon_end, self.epsilon * self.epsilon_decay)
            episode_rewards.append(total_reward)

        return episode_rewards
