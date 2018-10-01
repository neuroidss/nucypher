from logging import getLogger
from urllib.parse import urlparse

from constant_sorrow import constants
from eth_keys.datatypes import PublicKey, Signature
from eth_tester import EthereumTester
from eth_tester import PyEVMBackend
from eth_utils import to_canonical_address
from typing import Tuple, Union
from web3 import Web3, WebsocketProvider, HTTPProvider, IPCProvider
from web3.contract import Contract
from web3.providers.eth_tester.main import EthereumTesterProvider

from nucypher.blockchain.eth.constants import NUCYPHER_GAS_LIMIT
from nucypher.blockchain.eth.registry import EthereumContractRegistry
from nucypher.blockchain.eth.sol.compile import SolidityCompiler


class BlockchainInterface:
    """
    Interacts with a solidity compiler and a registry in order to instantiate compiled
    ethereum contracts with the given web3 provider backend.
    """
    __default_timeout = 10  # seconds
    __default_network = 'tester'
    # __default_transaction_gas_limit = 500000  # TODO: determine sensible limit and validate transactions

    class UnknownContract(Exception):
        pass

    class InterfaceError(Exception):
        pass

    def __init__(self,
                 network_name: str = None,
                 provider_uri: str = None,
                 providers: list = None,
                 autoconnect: bool = True,
                 timeout: int = None,
                 registry: EthereumContractRegistry = None,
                 compiler: SolidityCompiler=None) -> None:

        """
        A blockchain "network inerface"; The circumflex wraps entirely around the bounds of
        contract operations including compilation, deployment, and execution.


         Solidity Files -- SolidityCompiler ---                  --- HTTPProvider --
                                               |                |                   |
                                               |                |                    -- External EVM (geth, etc.)
                                                                                    |
                                               *BlockchainInterface* -- IPCProvider --

                                               |      |         |
                                               |      |         |
         Registry File -- ContractRegistry --       |          ---- TestProvider -- EthereumTester
                                                      |
                                                      |                                  |
                                                      |
                                                                                       Pyevm (development chain)
                                                 Blockchain

                                                      |

                                                    Agent ... (Contract API)

                                                      |

                                                Character / Actor


        The circumflex is the junction of the solidity compiler, a contract registry, and a collection of
        web3 network __providers as a means of interfacing with the ethereum blockchain to execute
        or deploy contract code on the network.


        Compiler and Registry Usage
        -----------------------------

        Contracts are freshly re-compiled if an instance of SolidityCompiler is passed; otherwise,
        The registry will read contract data saved to disk that is be used to retrieve contact address and op-codes.
        Optionally, A registry instance can be passed instead.


        Provider Usage
        ---------------
        https: // github.com / ethereum / eth - tester     # available-backends


        * HTTP Provider - supply endpiont_uri
        * Websocket Provider - supply endpoint uri and websocket=True
        * IPC Provider - supply IPC path
        * Custom Provider - supply an iterable of web3.py provider instances

        """

        self.log = getLogger("blockchain-interface")                       # type: Logger

        self.__network = network_name if network_name is not None else self.__default_network
        self.timeout = timeout if timeout is not None else self.__default_timeout

        #
        # Providers
        #

        self.w3 = constants.NO_BLOCKCHAIN_CONNECTION
        self.__providers = providers or constants.NO_BLOCKCHAIN_CONNECTION
        self.provider_uri = constants.NO_BLOCKCHAIN_CONNECTION

        if provider_uri and providers:
            raise self.InterfaceError("Pass a provider URI string, or a list of provider instances.")
        elif provider_uri:
            self.provider_uri = provider_uri
            self.add_provider(provider_uri=provider_uri)
        elif providers:
            self.provider_uri = constants.MANUAL_PROVIDERS_SET
            for provider in providers:
                self.add_provider(provider)
        else:
            self.log.warning("No provider supplied for new blockchain interface; Using defaults")

        # if a SolidityCompiler class instance was passed, compile from solidity source code
        recompile = True if compiler is not None else False
        self.__recompile = recompile
        self.__sol_compiler = compiler

        # Setup the registry and base contract factory cache
        registry = registry if registry is not None else EthereumContractRegistry()
        self.registry = registry
        self.log.info("Using contract registry {}".format(self.registry.filepath))

        if self.__recompile is True:
            # Execute the compilation if we're recompiling
            # Otherwise read compiled contract data from the registry
            interfaces = self.__sol_compiler.compile()
            __raw_contract_cache = interfaces
        else:
            __raw_contract_cache = constants.NO_COMPILATION_PERFORMED
        self.__raw_contract_cache = __raw_contract_cache

        # Auto-connect
        self.autoconnect = autoconnect
        if self.autoconnect is True:
            self.connect()

    def connect(self):
        self.log.info("Connecting to {}".format(self.provider_uri))

        if self.__providers is constants.NO_BLOCKCHAIN_CONNECTION:
            raise self.InterfaceError("There are no configured blockchain providers")

        # Connect
        web3_instance = Web3(providers=self.__providers)  # Instantiate Web3 object with provider
        self.w3 = web3_instance

        # Check connection
        if not self.is_connected:
            raise self.InterfaceError('Failed to connect to providers: {}'.format(self.__providers))

        if self.is_connected:
            self.log.info('Successfully Connected to {}'.format(self.provider_uri))
            return self.is_connected
        else:
            raise self.InterfaceError("Failed to connect to {}. Check your connection.".format(self.provider_uri))

    @property
    def providers(self) -> Tuple[Union[IPCProvider, WebsocketProvider, HTTPProvider], ...]:
        return tuple(self.__providers)

    @property
    def network(self) -> str:
        return self.__network

    @property
    def is_connected(self) -> bool:
        """
        https://web3py.readthedocs.io/en/stable/__providers.html#examples-using-automated-detection
        """
        return self.w3.isConnected()

    @property
    def version(self) -> str:
        """Return node version information"""
        return self.w3.version.node

    def add_provider(self,
                     provider: Union[IPCProvider, WebsocketProvider, HTTPProvider] = None,
                     provider_uri: str = None,
                     timeout: int = None) -> None:

        if not provider_uri and not provider:
            raise self.InterfaceError("No URI or provider instances supplied.")

        if provider_uri and not provider:
            uri_breakdown = urlparse(provider_uri)

            # PyEVM
            if uri_breakdown.scheme == 'tester':

                if uri_breakdown.netloc == 'pyevm':

                    # TODO: Update to newest eth-tester after #123 is merged
                    pyevm_backend = PyEVMBackend.from_genesis_overrides(parameter_overrides={'gas_limit': NUCYPHER_GAS_LIMIT})
                    eth_tester = EthereumTester(backend=pyevm_backend, auto_mine_transactions=True)
                    provider = EthereumTesterProvider(ethereum_tester=eth_tester)

                elif uri_breakdown.netloc == 'geth':
                    # TODO: Auto gethdev
                    # https://web3py.readthedocs.io/en/stable/providers.html  # geth-dev-proof-of-authority
                    # from web3.auto.gethdev import w3

                    # Hardcoded gethdev IPC provider
                    provider = IPCProvider(ipc_path='/tmp/geth.ipc', timeout=timeout)
                    # w3 = Web3(providers=(provider))
                    # w3.middleware_stack.inject(geth_poa_middleware, layer=0)

                else:
                    raise self.InterfaceError("{} is an ambiguous or unsupported blockchain provider URI".format(provider_uri))

            # IPC
            elif uri_breakdown.scheme == 'ipc':
                provider = IPCProvider(ipc_path=uri_breakdown.path, timeout=timeout)

            # Websocket
            elif uri_breakdown.scheme == 'ws':
                provider = WebsocketProvider(endpoint_uri=provider_uri)

            # HTTP
            elif uri_breakdown.scheme in ('http', 'https'):
                provider = HTTPProvider(endpoint_uri=provider_uri)

            else:
                raise self.InterfaceError("'{}' is not a blockchain provider protocol".format(uri_breakdown.scheme))

            # lazy
            if self.__providers is constants.NO_BLOCKCHAIN_CONNECTION:
                self.__providers = list()
            self.__providers.append(provider)

    def get_contract_factory(self, contract_name: str) -> Contract:
        """Retrieve compiled interface data from the cache and return web3 contract"""
        try:
            interface = self.__raw_contract_cache[contract_name]
        except KeyError:
            raise self.UnknownContract('{} is not a locally compiled contract.'.format(contract_name))
        except TypeError:
            if self.__raw_contract_cache is constants.NO_COMPILATION_PERFORMED:
                message = "The local contract compiler cache is empty because no compilation was performed."
                raise self.InterfaceError(message)
        else:
            contract = self.w3.eth.contract(abi=interface['abi'],
                                            bytecode=interface['bin'],
                                            ContractFactoryClass=Contract)
            return contract

    def _wrap_contract(self, dispatcher_contract: Contract,
                       target_contract: Contract, factory=Contract) -> Contract:
        """Used for upgradeable contracts."""

        # Wrap the contract
        wrapped_contract = self.w3.eth.contract(abi=target_contract.abi,
                                                address=dispatcher_contract.address,
                                                ContractFactoryClass=factory)
        return wrapped_contract

    def get_contract_by_address(self, address: str):
        """Read a single contract's data from the registrar and return it."""
        try:
            contract_records = self.registry.search(contract_address=address)
        except RuntimeError:
            raise self.InterfaceError('Corrupted Registrar')  # TODO: Integrate with Registry
        else:
            if not contract_records:
                raise self.InterfaceError("No such contract with address {}".format(address))
            return contract_records[0]

    def get_contract_by_name(self, name: str, upgradeable=False, factory=Contract) -> Contract:
        """
        Instantiate a deployed contract from registrar data,
        and assemble it with it's dispatcher if it is upgradeable.
        """
        target_contract_records = self.registry.search(contract_name=name)

        if not target_contract_records:
            raise self.InterfaceError("No such contract records with name {}".format(name))

        if upgradeable:
            # Lookup dispatchers; Search fot a published dispatcher that targets this contract record
            dispatcher_records = self.registry.search(contract_name='Dispatcher')

            matching_pairs = list()
            for dispatcher_name, dispatcher_addr, dispatcher_abi in dispatcher_records:

                dispatcher_contract = self.w3.eth.contract(abi=dispatcher_abi,
                                                           address=dispatcher_addr,
                                                           ContractFactoryClass=factory)

                # Read this dispatchers target address from the blockchain
                live_target_address = dispatcher_contract.functions.target().call()

                for target_name, target_addr, target_abi in target_contract_records:
                    if target_addr == live_target_address:
                        pair = dispatcher_addr, target_abi
                        matching_pairs.append(pair)

            else:  # for/else

                if len(matching_pairs) == 0:
                    raise self.InterfaceError("No dispatcher targets known contract records for {}".format(name))

                elif len(matching_pairs) > 1:
                    raise self.InterfaceError("There is more than one dispatcher targeting {}".format(name))

                selected_contract_address, selected_contract_abi = matching_pairs[0]
        else:
            if len(target_contract_records) != 1:  # TODO: Allow multiple non-upgradeable records (UserEscrow)
                m = "Multiple records returned from the registry for non-upgradeable contract {}"
                raise self.InterfaceError(m.format(name))

            selected_contract_name, selected_contract_address, selected_contract_abi = target_contract_records[0]

        # Create the contract from selected sources
        unified_contract = self.w3.eth.contract(abi=selected_contract_abi,
                                                address=selected_contract_address,
                                                ContractFactoryClass=factory)

        return unified_contract

    def call_backend_sign(self, account: str, message: bytes) -> str:
        """
        Calls the appropriate signing function for the specified account on the
        backend. If the backend is based on eth-tester, then it uses the
        eth-tester signing interface to do so.
        """
        provider = self.providers[0]  # TODO: Handle multiple providers
        if isinstance(provider, EthereumTesterProvider):
            address = to_canonical_address(account)
            sig_key = provider.ethereum_tester.backend._key_lookup[address]
            signed_message = sig_key.sign_msg(message)
            return signed_message
        else:
            return self.w3.eth.sign(account, data=message)  # TODO: Technically deprecated...

    def call_backend_verify(self, pubkey: PublicKey, signature: Signature, msg_hash: bytes):
        """
        Verifies a hex string signature and message hash are from the provided
        public key.
        """
        is_valid_sig = signature.verify_msg_hash(msg_hash, pubkey)
        sig_pubkey = signature.recover_public_key_from_msg_hash(msg_hash)

        return is_valid_sig and (sig_pubkey == pubkey)

    def unlock_account(self, address, password, duration):
        if self.provider_uri == 'tester://pyevm':  # TODO How to handle passwordless unlocked accounts in test
            return True
        return self.w3.personal.unlockAccount(address, password, duration)


class BlockchainDeployerInterface(BlockchainInterface):

    def __init__(self, deployer_address: str=None, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)  # Depends on web3 instance
        self.__deployer_address = deployer_address if deployer_address is not None else constants.NO_DEPLOYER_CONFIGURED

    @property
    def deployer_address(self):
        return self.__deployer_address

    @deployer_address.setter
    def deployer_address(self, checksum_address: str) -> None:
        if self.deployer_address is not constants.NO_DEPLOYER_CONFIGURED:
            raise RuntimeError("{} already has a deployer address set.".format(self.__class__.__name__))
        self.__deployer_address = checksum_address

    def deploy_contract(self, contract_name: str, *args, **kwargs) -> Tuple[Contract, str]:
        """
        Retrieve compiled interface data from the cache and
        return an instantiated deployed contract
        """
        if self.__deployer_address is constants.NO_DEPLOYER_CONFIGURED:
            raise self.InterfaceError('No deployer address is configured.')
        #
        # Build the deployment tx #
        #

        deploy_transaction = {'from': self.deployer_address, 'gasPrice': self.w3.eth.gasPrice}
        self.log.info("Deployer address is {}".format(deploy_transaction['from']))

        contract_factory = self.get_contract_factory(contract_name=contract_name)
        deploy_bytecode = contract_factory.constructor(*args, **kwargs).buildTransaction(deploy_transaction)
        self.log.info("Deploying contract: {}: {} bytes".format(contract_name, len(deploy_bytecode['data'])))

        #
        # Transmit the deployment tx #
        #
        txhash = contract_factory.constructor(*args, **kwargs).transact(transaction=deploy_transaction)
        self.log.info("{} Deployment TX sent : txhash {}".format(contract_name, txhash.hex()))

        # Wait for receipt
        receipt = self.w3.eth.waitForTransactionReceipt(txhash)
        address = receipt['contractAddress']
        self.log.info("Confirmed {} deployment: address {}".format(contract_name, address))

        #
        # Instantiate & enroll contract
        #
        contract = contract_factory(address=address)

        self.registry.enroll(contract_name=contract_name,
                             contract_address=contract.address,
                             contract_abi=contract_factory.abi)

        return contract, txhash
