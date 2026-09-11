import {
  Badge,
  Box,
  Button,
  Card,
  Flex,
  Heading,
  SimpleGrid,
  Spinner,
  Table,
  Text,
  VStack,
} from '@chakra-ui/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { FiCheckCircle, FiClock, FiPlay, FiSearch } from 'react-icons/fi'
import { investigationApi } from '../../lib/api'

function statusPalette(status: string): string {
  switch (status.toLowerCase()) {
    case 'pending':
      return 'gray'
    case 'in_progress':
      return 'brand'
    case 'completed':
      return 'green'
    case 'escalated':
      return 'red'
    default:
      return 'gray'
  }
}

function priorityPalette(priority: string): string {
  switch (priority.toLowerCase()) {
    case 'high':
    case 'critical':
      return 'red'
    case 'medium':
      return 'orange'
    case 'low':
      return 'green'
    default:
      return 'gray'
  }
}

function formatDate(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? '—'
    : date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

const WORKFLOW = [
  { step: '1. KYC Agent', detail: 'Identity verification and document checking', palette: 'teal' },
  { step: '2. AML Agent', detail: 'Transaction analysis and pattern detection', palette: 'orange' },
  { step: '3. Relationship Agent', detail: 'Network analysis over the context graph', palette: 'purple' },
  { step: '4. Compliance Agent', detail: 'Sanctions/PEP screening and SAR drafting', palette: 'red' },
]

export default function InvestigationPanel() {
  const [selectedInvestigation, setSelectedInvestigation] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const { data: investigations = [], isLoading } = useQuery({
    queryKey: ['investigations'],
    queryFn: () => investigationApi.list(),
  })

  const startMutation = useMutation({
    mutationFn: (id: string) => investigationApi.start(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['investigations'] })
    },
  })

  const countByStatus = (status: string) =>
    investigations.filter((i) => (i.status ?? '').toLowerCase() === status).length

  return (
    <Box>
      <Flex justify="space-between" align="center" mb={6}>
        <Box>
          <Heading size="lg" fontFamily="heading">
            Investigations
          </Heading>
          <Text color="fg.muted">Compliance investigations with multi-agent analysis</Text>
        </Box>
      </Flex>

      {/* Summary */}
      <SimpleGrid columns={{ base: 1, md: 3 }} gap={6} mb={8}>
        {[
          { label: 'Pending', value: countByStatus('pending'), icon: FiClock, palette: 'gray' },
          {
            label: 'In Progress',
            value: countByStatus('in_progress'),
            icon: FiSearch,
            palette: 'brand',
          },
          {
            label: 'Completed',
            value: countByStatus('completed'),
            icon: FiCheckCircle,
            palette: 'green',
          },
        ].map(({ label, value, icon: Icon, palette }) => (
          <Card.Root key={label} colorPalette={palette}>
            <Card.Body>
              <Flex align="center" gap={4}>
                <Box p={3} borderRadius="full" bg="colorPalette.muted" color="colorPalette.fg">
                  <Icon size={24} />
                </Box>
                <Box>
                  <Text color="fg.muted" fontSize="sm">
                    {label}
                  </Text>
                  <Text fontSize="2xl" fontWeight="bold">
                    {value}
                  </Text>
                </Box>
              </Flex>
            </Card.Body>
          </Card.Root>
        ))}
      </SimpleGrid>

      {/* Table */}
      <Card.Root>
        <Card.Header>
          <Heading size="md" fontFamily="heading">
            All Investigations
          </Heading>
        </Card.Header>
        <Card.Body>
          {isLoading ? (
            <Flex justify="center" py={8}>
              <Spinner size="lg" colorPalette="brand" />
            </Flex>
          ) : investigations.length === 0 ? (
            <VStack py={8} gap={4}>
              <Box color="fg.subtle">
                <FiSearch size={48} />
              </Box>
              <Text color="fg.muted">
                No investigations yet. Ask the advisor to investigate a customer, or create one
                through <code>POST /api/investigations</code>.
              </Text>
            </VStack>
          ) : (
            <Box overflowX="auto">
              <Table.Root>
                <Table.Header>
                  <Table.Row>
                    <Table.ColumnHeader>ID</Table.ColumnHeader>
                    <Table.ColumnHeader>Title</Table.ColumnHeader>
                    <Table.ColumnHeader>Customer</Table.ColumnHeader>
                    <Table.ColumnHeader>Status</Table.ColumnHeader>
                    <Table.ColumnHeader>Priority</Table.ColumnHeader>
                    <Table.ColumnHeader>Created</Table.ColumnHeader>
                    <Table.ColumnHeader>Actions</Table.ColumnHeader>
                  </Table.Row>
                </Table.Header>
                <Table.Body>
                  {investigations.map((investigation) => (
                    <Table.Row
                      key={investigation.id}
                      cursor="pointer"
                      _hover={{ bg: 'bg.muted' }}
                      onClick={() => setSelectedInvestigation(investigation.id)}
                      bg={selectedInvestigation === investigation.id ? 'bg.emphasized' : undefined}
                    >
                      <Table.Cell fontFamily="mono" fontSize="sm">
                        {investigation.id}
                      </Table.Cell>
                      <Table.Cell fontWeight="medium" maxW="250px" truncate>
                        {investigation.title}
                      </Table.Cell>
                      <Table.Cell fontFamily="mono" fontSize="sm">
                        {investigation.customer_id}
                      </Table.Cell>
                      <Table.Cell>
                        <Badge colorPalette={statusPalette(investigation.status)}>
                          {investigation.status?.replace(/_/g, ' ')}
                        </Badge>
                      </Table.Cell>
                      <Table.Cell>
                        <Badge colorPalette={priorityPalette(investigation.priority)}>
                          {investigation.priority}
                        </Badge>
                      </Table.Cell>
                      <Table.Cell fontSize="sm" color="fg.muted">
                        {formatDate(investigation.created_at)}
                      </Table.Cell>
                      <Table.Cell>
                        {(investigation.status ?? '').toLowerCase() === 'pending' && (
                          <Button
                            size="sm"
                            colorPalette="brand"
                            variant="outline"
                            onClick={(event) => {
                              event.stopPropagation()
                              startMutation.mutate(investigation.id)
                            }}
                            loading={
                              startMutation.isPending && startMutation.variables === investigation.id
                            }
                          >
                            <FiPlay />
                            Start
                          </Button>
                        )}
                      </Table.Cell>
                    </Table.Row>
                  ))}
                </Table.Body>
              </Table.Root>
            </Box>
          )}
          {startMutation.isError && (
            <Text mt={3} fontSize="sm" color="fg.error">
              Could not start the investigation:{' '}
              {startMutation.error instanceof Error
                ? startMutation.error.message
                : 'request failed'}
            </Text>
          )}
        </Card.Body>
      </Card.Root>

      {/* Workflow explainer */}
      <Card.Root mt={6}>
        <Card.Header>
          <Heading size="md" fontFamily="heading">
            Multi-Agent Investigation Workflow
          </Heading>
        </Card.Header>
        <Card.Body>
          <SimpleGrid columns={{ base: 1, md: 2, lg: 4 }} gap={4}>
            {WORKFLOW.map(({ step, detail, palette }) => (
              <Box key={step} p={4} colorPalette={palette} bg="colorPalette.subtle" borderRadius="md">
                <Text fontWeight="bold" color="colorPalette.fg">
                  {step}
                </Text>
                <Text fontSize="sm" color="fg.muted">
                  {detail}
                </Text>
              </Box>
            ))}
          </SimpleGrid>
        </Card.Body>
      </Card.Root>
    </Box>
  )
}
