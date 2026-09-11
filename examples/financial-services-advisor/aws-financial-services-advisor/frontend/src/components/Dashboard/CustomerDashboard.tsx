import {
  Badge,
  Box,
  Card,
  Flex,
  Heading,
  Input,
  SimpleGrid,
  Spinner,
  Table,
  Text,
} from '@chakra-ui/react'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { FiAlertTriangle, FiSearch, FiTrendingUp, FiUsers } from 'react-icons/fi'
import { alertApi, countByStatus, customerApi } from '../../lib/api'

interface StatCardProps {
  label: string
  value: string | number
  icon: React.ElementType
  colorPalette: string
}

function StatCard({ label, value, icon: IconComponent, colorPalette }: StatCardProps) {
  return (
    <Card.Root colorPalette={colorPalette}>
      <Card.Body>
        <Flex justify="space-between" align="center">
          <Box>
            <Text color="fg.muted" fontSize="sm" fontWeight="medium">
              {label}
            </Text>
            <Text fontSize="2xl" fontWeight="bold">
              {value}
            </Text>
          </Box>
          <Box p={3} borderRadius="full" bg="colorPalette.muted" color="colorPalette.fg">
            <IconComponent size={24} />
          </Box>
        </Flex>
      </Card.Body>
    </Card.Root>
  )
}

/** Risk levels arrive upper-case from the backend; compare case-insensitively. */
function riskPalette(riskLevel: string): string {
  switch (riskLevel.toLowerCase()) {
    case 'critical':
      return 'red'
    case 'high':
      return 'orange'
    case 'medium':
      return 'yellow'
    case 'low':
      return 'green'
    default:
      return 'gray'
  }
}

export default function CustomerDashboard() {
  const [searchQuery, setSearchQuery] = useState('')

  const { data: customersData, isLoading: customersLoading } = useQuery({
    queryKey: ['customers'],
    queryFn: () => customerApi.list(),
  })

  const { data: alertSummary } = useQuery({
    queryKey: ['alertSummary'],
    queryFn: () => alertApi.getSummary(),
  })

  const customers = customersData?.customers ?? []
  const totalCustomers = customersData?.total ?? 0

  const needle = searchQuery.toLowerCase()
  const filteredCustomers = customers.filter(
    (customer) =>
      customer.name.toLowerCase().includes(needle) || customer.id.toLowerCase().includes(needle),
  )

  const highRiskCount = customers.filter((customer) =>
    ['high', 'critical'].includes((customer.risk_level ?? '').toLowerCase()),
  ).length

  return (
    <Box>
      <Flex justify="space-between" align="center" mb={6}>
        <Box>
          <Heading size="lg" fontFamily="heading">
            Dashboard
          </Heading>
          <Text color="fg.muted">Financial compliance overview</Text>
        </Box>
      </Flex>

      {/* Stats */}
      <SimpleGrid columns={{ base: 1, md: 2, lg: 4 }} gap={6} mb={8}>
        <StatCard
          label="Total Customers"
          value={totalCustomers}
          icon={FiUsers}
          colorPalette="brand"
        />
        <StatCard label="High Risk" value={highRiskCount} icon={FiTrendingUp} colorPalette="red" />
        <StatCard
          label="New Alerts"
          value={countByStatus(alertSummary, 'new')}
          icon={FiAlertTriangle}
          colorPalette="orange"
        />
        <StatCard
          label="Under Investigation"
          value={countByStatus(alertSummary, 'investigating')}
          icon={FiSearch}
          colorPalette="purple"
        />
      </SimpleGrid>

      {/* Customer list */}
      <Card.Root>
        <Card.Header>
          <Flex justify="space-between" align="center" gap={4} flexWrap="wrap">
            <Heading size="md" fontFamily="heading">
              Customers
            </Heading>
            <Input
              placeholder="Search customers…"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              maxW="300px"
            />
          </Flex>
        </Card.Header>
        <Card.Body>
          {customersLoading ? (
            <Flex justify="center" py={8}>
              <Spinner size="lg" colorPalette="brand" />
            </Flex>
          ) : filteredCustomers.length === 0 ? (
            <Text color="fg.muted" textAlign="center" py={8}>
              No customers found. Load the sample data with <code>make load-data</code>.
            </Text>
          ) : (
            <Box overflowX="auto">
              <Table.Root>
                <Table.Header>
                  <Table.Row>
                    <Table.ColumnHeader>ID</Table.ColumnHeader>
                    <Table.ColumnHeader>Name</Table.ColumnHeader>
                    <Table.ColumnHeader>Type</Table.ColumnHeader>
                    <Table.ColumnHeader>Jurisdiction</Table.ColumnHeader>
                    <Table.ColumnHeader>Risk Level</Table.ColumnHeader>
                    <Table.ColumnHeader>Risk Score</Table.ColumnHeader>
                    <Table.ColumnHeader>KYC</Table.ColumnHeader>
                  </Table.Row>
                </Table.Header>
                <Table.Body>
                  {filteredCustomers.map((customer) => (
                    <Table.Row key={customer.id}>
                      <Table.Cell fontFamily="mono" fontSize="sm">
                        {customer.id}
                      </Table.Cell>
                      <Table.Cell fontWeight="medium">{customer.name}</Table.Cell>
                      <Table.Cell textTransform="capitalize">{customer.type}</Table.Cell>
                      <Table.Cell>{customer.jurisdiction ?? '—'}</Table.Cell>
                      <Table.Cell>
                        <Badge colorPalette={riskPalette(customer.risk_level)}>
                          {customer.risk_level}
                        </Badge>
                      </Table.Cell>
                      <Table.Cell>{customer.risk_score ?? '—'}</Table.Cell>
                      <Table.Cell textTransform="capitalize">
                        {customer.kyc_status ?? 'unknown'}
                      </Table.Cell>
                    </Table.Row>
                  ))}
                </Table.Body>
              </Table.Root>
            </Box>
          )}
        </Card.Body>
      </Card.Root>
    </Box>
  )
}
